"""Persistence helpers for goal alignment events."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

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


def record_alignment_event(event: dict[str, Any]) -> None:
    """Persist a goal alignment event to disk."""

    try:
        _alignment_log.append(event)
    except Exception:  # pragma: no cover - persistence failures should not break planner
        pass


__all__ = ["record_alignment_event"]
