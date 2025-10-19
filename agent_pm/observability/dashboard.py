"""Helpers to build queue health dashboards."""

from __future__ import annotations

from dataclasses import dataclass

from ..storage.redis import count_dead_letters
from ..storage.tasks import get_task_queue


@dataclass
class QueueHealth:
    queue_name: str
    dead_letters: int
    auto_triage_enabled: bool


async def gather_queue_health() -> QueueHealth:
    queue = await get_task_queue()
    client = await queue.get_client()  # type: ignore[attr-defined]
    dead_letters = await count_dead_letters(client)
    auto_triage_enabled = bool(queue)
    return QueueHealth(queue_name=getattr(queue, "queue_name", "unknown"), dead_letters=dead_letters, auto_triage_enabled=auto_triage_enabled)
