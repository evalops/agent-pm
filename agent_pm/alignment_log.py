"""Persistence helpers, analytics, and realtime fan-out for goal alignment events."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import Counter
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from sqlalchemy import select

from .database import AlignmentEvent, get_session_factory
from .metrics import record_alignment_followup
from .plugins import plugin_registry
from .settings import settings
from .utils.datetime import utc_now, utc_now_isoformat


class AlignmentLog:
    def __init__(self, path: Path, max_entries: int = 500) -> None:
        self.path = path
        self.max_entries = max_entries
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")

    def load(self) -> list[dict[str, Any]]:
        try:
            content = self.path.read_text(encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            return []
        return []

    def append(self, event: dict[str, Any]) -> None:
        events = self.load()
        event.setdefault("timestamp", utc_now_isoformat())
        events.append(event)
        if len(events) > self.max_entries:
            events = events[-self.max_entries :]
        self.save(events)

    def save(self, events: list[dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(events, indent=2), encoding="utf-8")

    def update(self, event_id: str, mutator: Callable[[dict[str, Any]], None]) -> bool:
        events = self.load()
        updated = False
        for entry in events:
            if entry.get("event_id") == event_id:
                mutator(entry)
                updated = True
                break
        if updated:
            self.save(events)
        return updated


_alignment_log = AlignmentLog(settings.alignment_log_path)


def _database_configured() -> bool:
    return bool(settings.database_url)


async def _persist_event_db(event: dict[str, Any]) -> None:
    if not _database_configured():  # pragma: no cover - deployment dependent
        return

    session_factory = get_session_factory()
    async with session_factory() as session:
        record = AlignmentEvent(
            event_id=event.get("event_id"),
            title=event.get("title", ""),
            context=event.get("context"),
            suggestions=event.get("suggestions", []),
            notification_status=event.get("notification", {}).get("status", "unknown"),
            notification_meta=event.get("notification", {}),
            created_at=utc_now(),
        )
        session.add(record)
        await session.commit()


def record_alignment_event(event: dict[str, Any]) -> dict[str, Any]:
    """Persist a goal alignment event and fan-out realtime notifications."""

    enriched: dict[str, Any] = dict(event)
    enriched.setdefault("event_id", uuid.uuid4().hex)
    enriched.setdefault("created_at", utc_now_isoformat())

    notification = enriched.get("notification")
    if not isinstance(notification, dict):
        notification = {"status": notification} if notification is not None else {}
    enriched["notification"] = notification

    suggestions = enriched.get("suggestions")
    if suggestions is None:
        suggestions = []
    elif not isinstance(suggestions, list):
        suggestions = [suggestions]
    enriched["suggestions"] = suggestions

    with suppress(Exception):  # pragma: no cover - persistence best-effort
        _alignment_log.append(enriched)

    with suppress(Exception):  # pragma: no cover - realtime fan-out best-effort
        from .alignment_stream import broadcast_alignment_event

        broadcast_alignment_event(enriched)

    if _database_configured():
        with suppress(Exception):  # pragma: no cover - best-effort persistence

            async def _runner() -> None:
                await _persist_event_db(enriched)

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(_runner())
            else:
                loop.create_task(_runner())

    plugin_registry.fire("post_alignment_event", event=enriched)
    return enriched


async def fetch_alignment_events(limit: int = 50) -> list[dict[str, Any]]:
    if _database_configured():
        session_factory = get_session_factory()
        async with session_factory() as session:
            result = await session.execute(
                select(AlignmentEvent).order_by(AlignmentEvent.created_at.desc()).limit(limit)
            )
            records: list[dict[str, Any]] = []
            for row in result.scalars():
                data = {
                    "event_id": row.event_id,
                    "title": row.title,
                    "context": row.context,
                    "suggestions": row.suggestions or [],
                    "notification": row.notification_meta or {"status": row.notification_status},
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                if row.followup_status or row.followup_recorded_at:
                    data["followup"] = {
                        "status": row.followup_status,
                        "recorded_at": row.followup_recorded_at.isoformat() if row.followup_recorded_at else None,
                    }
                records.append(data)
            return records

    events = _alignment_log.load()

    def _ensure_metadata(entry: dict[str, Any]) -> dict[str, Any]:
        if not entry.get("event_id"):
            entry["event_id"] = uuid.uuid4().hex
        return entry

    recent = [_ensure_metadata(entry) for entry in events[-limit:]]
    return list(reversed(recent))


def summarize_alignment_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter()
    idea_counts = Counter()

    for event in events:
        status = event.get("notification", {}).get("status", "unknown")
        status_counts[status] += 1
        for suggestion in event.get("suggestions", []):
            idea = suggestion.get("idea") or "unknown"
            idea_counts[idea] += 1

    top_ideas = idea_counts.most_common(5)
    return {
        "total_events": len(events),
        "status_counts": dict(status_counts),
        "top_ideas": top_ideas,
    }


def get_alignment_summary(limit: int = 50) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events = asyncio.run(fetch_alignment_events(limit))
    summary = summarize_alignment_events(events)
    return events, summary


async def _persist_followup_db(event_id: str, status: str) -> bool:
    if not _database_configured():  # pragma: no cover - deployment dependent
        return False

    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(AlignmentEvent).where(AlignmentEvent.event_id == event_id).with_for_update()
        )
        record = result.scalar_one_or_none()
        if record is None:
            return False
        record.followup_status = status
        record.followup_recorded_at = utc_now()
        await session.commit()
        return True


async def record_alignment_followup_event(event_id: str, status: str) -> bool:
    captured: dict[str, Any] | None = None

    def _mutator(entry: dict[str, Any]) -> None:
        nonlocal captured
        followup = entry.setdefault("followup", {})
        followup["status"] = status
        followup["recorded_at"] = utc_now_isoformat()
        captured = dict(entry)

    local_updated = _alignment_log.update(event_id, _mutator)
    db_updated = await _persist_followup_db(event_id, status)

    if captured is None and db_updated:
        events = await fetch_alignment_events(limit=100)
        for entry in events:
            if entry.get("event_id") == event_id:
                captured = entry
                break

    if local_updated or db_updated:
        record_alignment_followup(status)
        if captured is not None:
            with suppress(Exception):  # pragma: no cover - best effort broadcast
                from .alignment_stream import broadcast_alignment_event

                broadcast_alignment_event(captured)
            plugin_registry.fire("post_alignment_followup", event=captured, status=status)
        return True
    return False


__all__ = [
    "record_alignment_event",
    "fetch_alignment_events",
    "summarize_alignment_events",
    "get_alignment_summary",
    "record_alignment_followup_event",
]
