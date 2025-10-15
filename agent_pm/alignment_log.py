"""Persistence helpers and analytics for goal alignment events."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from .database import AlignmentEvent, get_session_factory
from .settings import settings


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
        event.setdefault("timestamp", datetime.utcnow().isoformat())
        events.append(event)
        if len(events) > self.max_entries:
            events = events[-self.max_entries :]
        self.path.write_text(json.dumps(events, indent=2), encoding="utf-8")


_alignment_log = AlignmentLog(settings.alignment_log_path)


def _database_configured() -> bool:
    return bool(settings.database_url)


async def _persist_event_db(event: dict[str, Any]) -> None:
    if not _database_configured():  # pragma: no cover - dependent on deployment config
        return

    session_factory = get_session_factory()
    async with session_factory() as session:
        record = AlignmentEvent(
            title=event.get("title", ""),
            context=event.get("context"),
            suggestions=event.get("suggestions", []),
            notification_status=event.get("notification", {}).get("status", "unknown"),
            notification_meta=event.get("notification", {}),
            created_at=datetime.utcnow(),
        )
        session.add(record)
        await session.commit()


def record_alignment_event(event: dict[str, Any]) -> None:
    """Persist a goal alignment event to disk (and database when configured)."""

    try:
        _alignment_log.append(event)
    except Exception:  # pragma: no cover - persistence failures should not break planner
        pass

    if not _database_configured():
        return

    try:
        async def _runner() -> None:
            await _persist_event_db(event)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_runner())
        else:
            loop.create_task(_runner())
    except Exception:  # pragma: no cover - best-effort persistence
        pass


async def fetch_alignment_events(limit: int = 50) -> list[dict[str, Any]]:
    if _database_configured():
        session_factory = get_session_factory()
        async with session_factory() as session:
            result = await session.execute(
                select(AlignmentEvent).order_by(AlignmentEvent.created_at.desc()).limit(limit)
            )
            records = []
            for row in result.scalars():
                records.append(
                    {
                        "title": row.title,
                        "context": row.context,
                        "suggestions": row.suggestions or [],
                        "notification": row.notification_meta or {"status": row.notification_status},
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                    }
                )
            return records

    # Fallback to log file
    events = _alignment_log.load()
    return list(reversed(events[-limit:]))


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


__all__ = [
    "record_alignment_event",
    "fetch_alignment_events",
    "summarize_alignment_events",
    "get_alignment_summary",
]
