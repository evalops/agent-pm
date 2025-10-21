"""Helpers to build queue health dashboards."""

from __future__ import annotations

from dataclasses import dataclass

from agent_pm.settings import settings
from agent_pm.storage.redis import count_dead_letters
from agent_pm.storage.tasks import get_task_queue


@dataclass
class QueueHealth:
    queue_name: str
    dead_letters: int
    auto_triage_enabled: bool


async def gather_queue_health() -> QueueHealth:
    queue = await get_task_queue()
    client = getattr(queue, "_redis", None)
    dead_letters = await count_dead_letters(client) if client else 0
    auto_triage_enabled = bool(settings.task_queue_auto_requeue_errors)
    return QueueHealth(
        queue_name=getattr(queue, "queue_name", "unknown"),
        dead_letters=dead_letters,
        auto_triage_enabled=auto_triage_enabled,
    )
