"""Trace utilities for persisting and inspecting planner traces."""

from __future__ import annotations

import json
from pathlib import Path

from ..memory import TraceMemory
from ..settings import settings
from ..utils.datetime import utc_now


def _trace_dir() -> Path:
    trace_dir = settings.trace_dir
    trace_dir.mkdir(parents=True, exist_ok=True)
    return trace_dir


def _safe_name(name: str) -> str:
    if ".." in name or "/" in name or "\\" in name:
        raise ValueError("invalid trace name")
    return name


def persist_trace(title: str, trace: TraceMemory) -> Path:
    trace_dir = _trace_dir()
    timestamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    slug = title.replace(" ", "_") or "untitled"
    path = trace_dir / f"{timestamp}-{slug}.json"
    data = trace.dump()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def list_traces(limit: int = 10) -> list[dict[str, str]]:
    trace_dir = _trace_dir()
    files = sorted(trace_dir.glob("*.json"), reverse=True)
    entries: list[dict[str, str]] = []
    for path in files[: max(limit, 0)]:
        timestamp, _, remainder = path.stem.partition("-")
        title_slug = remainder or ""
        entries.append(
            {
                "name": path.name,
                "timestamp": timestamp,
                "title_slug": title_slug,
            }
        )
    return entries


def load_trace(name: str) -> list[dict[str, str]]:
    trace_dir = _trace_dir()
    safe = _safe_name(name)
    path = trace_dir / safe
    if not path.exists():
        raise FileNotFoundError(safe)
    return json.loads(path.read_text(encoding="utf-8"))


def summarize_trace(name: str) -> dict[str, object]:
    events = load_trace(name)
    attempts = 0
    revisions = 0
    critic_status: str | None = None
    for event in events:
        if event.get("role") != "meta":
            if event.get("role") == "critic":
                critic_status = json.loads(event["content"]).get("status") if event.get("content") else critic_status
            continue
        try:
            payload = json.loads(event.get("content", "{}"))
        except json.JSONDecodeError:
            continue
        if payload.get("event") == "planner_attempt":
            attempts += 1
        if payload.get("event") == "planner_revision_requested":
            revisions += 1
    return {
        "name": name,
        "attempts": attempts,
        "revisions": revisions,
        "critic_status": critic_status,
        "events": events,
    }


__all__ = ["persist_trace", "list_traces", "load_trace", "summarize_trace"]
