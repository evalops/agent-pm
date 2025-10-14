"""Async task queue for background job processing with retries."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


@dataclass
class Task:
    """Represents a background task."""

    task_id: str
    name: str
    coro_fn: Callable[..., Coroutine[Any, Any, Any]]
    args: tuple
    kwargs: dict
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = None  # type: ignore[assignment]
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: Any = None
    error: str | None = None
    retry_count: int = 0
    max_retries: int = 3

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow()


class TaskQueue:
    """In-memory async task queue with retry logic."""

    def __init__(self, max_workers: int = 5):
        self.max_workers = max_workers
        self.queue: deque[Task] = deque()
        self.tasks: dict[str, Task] = {}
        self.workers: list[asyncio.Task[None]] = []
        self.running = False
        self._lock = asyncio.Lock()

    def start(self):
        """Start background workers."""
        if self.running:
            return
        self.running = True
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker(i))
            self.workers.append(worker)
        logger.info("TaskQueue started with %d workers", self.max_workers)

    async def stop(self):
        """Stop background workers gracefully."""
        self.running = False
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()
        logger.info("TaskQueue stopped")

    async def enqueue(
        self,
        name: str,
        coro_fn: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        max_retries: int = 3,
        **kwargs: Any,
    ) -> str:
        """Enqueue a task and return task ID."""
        task_id = str(uuid.uuid4())
        task = Task(
            task_id=task_id,
            name=name,
            coro_fn=coro_fn,
            args=args,
            kwargs=kwargs,
            max_retries=max_retries,
        )
        async with self._lock:
            self.queue.append(task)
            self.tasks[task_id] = task
        logger.info("Task enqueued: %s (id=%s)", name, task_id)
        return task_id

    async def get_task(self, task_id: str) -> Task | None:
        """Retrieve task by ID."""
        async with self._lock:
            return self.tasks.get(task_id)

    async def list_tasks(self, status: TaskStatus | None = None, limit: int = 50) -> list[Task]:
        """List tasks, optionally filtered by status."""
        async with self._lock:
            tasks = list(self.tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        # Sort by created_at descending
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks[:limit]

    async def _worker(self, worker_id: int):
        """Background worker that processes tasks."""
        logger.info("Worker %d started", worker_id)
        while self.running:
            task = None
            async with self._lock:
                if self.queue:
                    task = self.queue.popleft()

            if task is None:
                await asyncio.sleep(0.1)
                continue

            await self._execute_task(task)

        logger.info("Worker %d stopped", worker_id)

    async def _execute_task(self, task: Task):
        """Execute a task with retry logic."""
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.utcnow()
        logger.info("Executing task: %s (id=%s, attempt=%d)", task.name, task.task_id, task.retry_count + 1)

        try:
            result = await task.coro_fn(*task.args, **task.kwargs)
            task.status = TaskStatus.COMPLETED
            task.result = result
            task.completed_at = datetime.utcnow()
            logger.info("Task completed: %s (id=%s)", task.name, task.task_id)
        except Exception as exc:
            task.error = str(exc)
            task.retry_count += 1
            logger.error("Task failed: %s (id=%s, attempt=%d): %s", task.name, task.task_id, task.retry_count, exc)

            if task.retry_count < task.max_retries:
                task.status = TaskStatus.RETRYING
                # Exponential backoff: wait before re-enqueuing
                await asyncio.sleep(min(2**task.retry_count, 60))
                async with self._lock:
                    self.queue.append(task)
                logger.info("Task re-enqueued: %s (id=%s)", task.name, task.task_id)
            else:
                task.status = TaskStatus.FAILED
                task.completed_at = datetime.utcnow()
                logger.error("Task permanently failed: %s (id=%s)", task.name, task.task_id)


# Global task queue instance
_task_queue: TaskQueue | None = None


def get_task_queue() -> TaskQueue:
    """Get or create the global task queue."""
    global _task_queue
    if _task_queue is None:
        _task_queue = TaskQueue(max_workers=5)
        _task_queue.start()
    return _task_queue
