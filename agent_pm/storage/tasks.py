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

from ..observability.metrics import (
    record_task_completion,
    record_task_enqueued,
    record_task_latency,
)
from ..settings import settings
from ..utils.datetime import utc_now
from .redis import (
    clear_dead_letter,
    enqueue_task as redis_enqueue_task,
    fetch_dead_letters,
    get_redis_client,
    list_heartbeats,
    pop_task,
    record_dead_letter,
    set_task_result,
    write_heartbeat,
)

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
            self.created_at = utc_now()


class TaskQueue:
    """In-memory async task queue with retry logic."""

    def __init__(self, max_workers: int = 5):
        self.max_workers = max_workers
        self.queue: deque[Task] = deque()
        self.tasks: dict[str, Task] = {}
        self.workers: list[asyncio.Task[None]] = []
        self.running = False
        self._lock = asyncio.Lock()
        self.queue_name = "memory"

    async def start(self):
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
        record_task_enqueued(self.queue_name)
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
        task.started_at = utc_now()
        logger.info(
            "Executing task: %s (id=%s, attempt=%d)",
            task.name,
            task.task_id,
            task.retry_count + 1,
        )

        try:
            result = await task.coro_fn(*task.args, **task.kwargs)
            task.status = TaskStatus.COMPLETED
            task.result = result
            task.completed_at = utc_now()
            record_task_completion(self.queue_name, task.status.value)
            record_task_latency(self.queue_name, (task.completed_at - task.started_at).total_seconds())
            logger.info("Task completed: %s (id=%s)", task.name, task.task_id)
        except Exception as exc:
            task.error = str(exc)
            task.retry_count += 1
            logger.error(
                "Task failed: %s (id=%s, attempt=%d): %s",
                task.name,
                task.task_id,
                task.retry_count,
                exc,
            )

            if task.retry_count < task.max_retries:
                task.status = TaskStatus.RETRYING
                # Exponential backoff: wait before re-enqueuing
                await asyncio.sleep(min(2**task.retry_count, 60))
                async with self._lock:
                    self.queue.append(task)
                logger.info("Task re-enqueued: %s (id=%s)", task.name, task.task_id)
            else:
                task.status = TaskStatus.FAILED
                task.completed_at = utc_now()
                record_task_completion(self.queue_name, task.status.value)
                record_task_latency(self.queue_name, (task.completed_at - task.started_at).total_seconds())
                logger.error("Task permanently failed: %s (id=%s)", task.name, task.task_id)

    async def list_dead_letters(self, limit: int = 100) -> list[dict[str, Any]]:
        return []

    async def delete_dead_letter(self, task_id: str) -> None:
        return None

    async def worker_heartbeats(self) -> dict[str, Any]:
        return {}


# Global task queue instance
_task_queue: TaskQueue | None = None


async def get_task_queue() -> TaskQueue:
    """Get or create the global task queue."""
    global _task_queue
    if _task_queue is None:
        backend = settings.task_queue_backend
        if backend == "redis":
            client = await get_redis_client()

            class RedisTaskQueue(TaskQueue):
                def __init__(self, max_workers: int = 5):
                    super().__init__(max_workers=max_workers)
                    self.queue_name = "redis"
                    self._redis = client
                    self._registry: dict[str, Callable[..., Coroutine[Any, Any, Any]]] = {}
                    self._poll_interval = settings.task_queue_poll_interval
                    self._task_timeout = settings.task_queue_task_timeout
                    self._backoff_base = settings.task_queue_retry_backoff_base
                    self._backoff_max = settings.task_queue_retry_backoff_max
                    self._heartbeat_ttl = settings.task_queue_worker_heartbeat_ttl

                def register(self, name: str, coro_fn: Callable[..., Coroutine[Any, Any, Any]]) -> None:
                    self._registry[name] = coro_fn

                async def enqueue(  # type: ignore[override]
                    self,
                    name: str,
                    coro_fn: Callable[..., Coroutine[Any, Any, Any]],
                    *args: Any,
                    max_retries: int = 3,
                    **kwargs: Any,
                ) -> str:
                    self.register(name, coro_fn)
                    payload = {
                        "task_id": uuid.uuid4().hex,
                        "name": name,
                        "args": args,
                        "kwargs": kwargs,
                        "max_retries": max_retries,
                        "enqueued_at": utc_now().isoformat(),
                    }
                    await redis_enqueue_task(self._redis, name, payload)
                    record_task_enqueued(self.queue_name)
                    logger.info("Redis task enqueued: %s (id=%s)", name, payload["task_id"])
                    return payload["task_id"]

                async def pop(self) -> dict[str, Any] | None:
                    payload = await pop_task(self._redis)
                    if not payload:
                        return None
                    return payload

                async def _worker(self, worker_id: int):
                    logger.info("Redis worker %d started", worker_id)
                    while self.running:
                        payload = await self.pop()
                        if not payload:
                            await asyncio.sleep(self._poll_interval)
                            continue

                        task_id = payload.get("task_id", "unknown")
                        name = payload.get("name")
                        coro_fn = self._registry.get(name)
                        if coro_fn is None:
                            logger.error("No registered task callable for %s", name)
                            await record_dead_letter(
                                self._redis,
                                {**payload, "error": "missing_callable", "worker_id": worker_id},
                            )
                            record_task_completion(self.queue_name, TaskStatus.FAILED.value)
                            continue

                        retries = payload.get("retry_count", 0)
                        max_retries = payload.get("max_retries", 3)

                        start = utc_now()
                        try:
                            result = await asyncio.wait_for(
                                coro_fn(*payload.get("args", ()), **payload.get("kwargs", {})),
                                timeout=self._task_timeout,
                            )
                        except asyncio.TimeoutError:
                            payload["retry_count"] = retries + 1
                            payload["last_error"] = "timeout"
                            payload["worker_id"] = worker_id
                            await record_dead_letter(self._redis, payload)
                            record_task_completion(self.queue_name, TaskStatus.FAILED.value)
                            record_task_latency(self.queue_name, (utc_now() - start).total_seconds())
                            continue
                        except Exception as exc:  # pylint: disable=broad-except
                            retries += 1
                            payload["retry_count"] = retries
                            payload["last_error"] = str(exc)
                            if retries >= max_retries:
                                payload["worker_id"] = worker_id
                                await record_dead_letter(self._redis, payload)
                                record_task_completion(self.queue_name, TaskStatus.FAILED.value)
                                record_task_latency(self.queue_name, (utc_now() - start).total_seconds())
                                continue

                            backoff = min(self._backoff_base**retries, self._backoff_max)
                            await asyncio.sleep(backoff)
                            await redis_enqueue_task(self._redis, name, payload)
                            continue

                        await set_task_result(self._redis, task_id, {"status": "completed", "result": result})
                        record_task_completion(self.queue_name, TaskStatus.COMPLETED.value)
                        record_task_latency(self.queue_name, (utc_now() - start).total_seconds())

                        heartbeat_payload = {
                            "worker_id": worker_id,
                            "task_id": task_id,
                            "name": name,
                            "completed_at": utc_now().isoformat(),
                        }
                        await write_heartbeat(self._redis, f"worker:{worker_id}", heartbeat_payload, self._heartbeat_ttl)

                    logger.info("Redis worker %d stopped", worker_id)

                async def list_dead_letters(self, limit: int = 100) -> list[dict[str, Any]]:
                    return await fetch_dead_letters(self._redis, limit)

                async def delete_dead_letter(self, task_id: str) -> None:
                    await clear_dead_letter(self._redis, task_id)

                async def worker_heartbeats(self) -> dict[str, dict[str, Any]]:
                    return await list_heartbeats(self._redis)

            _task_queue = RedisTaskQueue(max_workers=settings.task_queue_workers)
        else:
            _task_queue = TaskQueue(max_workers=settings.task_queue_workers)
            await _task_queue.start()
    return _task_queue
