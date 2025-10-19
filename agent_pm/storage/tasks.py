"""Async task queue for background job processing with retries."""

from __future__ import annotations

import asyncio
import logging
import traceback
import uuid
from collections import deque
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from ..observability.metrics import (
    dead_letter_active_gauge,
    dead_letter_alert_total,
    dead_letter_auto_requeue_total,
    dead_letter_purged_total,
    dead_letter_recorded_total,
    dead_letter_requeued_total,
    record_task_completion,
    record_task_enqueued,
    record_task_latency,
)
from ..settings import settings
from ..utils.datetime import utc_now
from ..clients.slack_client import slack_client
from .redis import (
    append_dead_letter_audit,
    clear_dead_letter,
    count_dead_letters,
    delete_retry_policy,
    enqueue_task as redis_enqueue_task,
    fetch_dead_letter_audit,
    fetch_dead_letters,
    get_dead_letter,
    get_redis_client,
    get_retry_policy,
    list_heartbeats,
    list_retry_policies,
    pop_task,
    purge_dead_letters,
    record_dead_letter,
    set_retry_policy,
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
    metadata: dict[str, Any] = field(default_factory=dict)
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
        metadata: dict[str, Any] | None = None,
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
            metadata=metadata or {},
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

    async def list_dead_letters(
        self,
        limit: int = 100,
        offset: int = 0,
        workflow_id: str | None = None,
        error_type: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        return [], 0

    async def list_dead_letter_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        return []

    async def get_retry_policy(self, task_name: str) -> dict[str, Any] | None:
        return None

    async def set_retry_policy(self, task_name: str, policy: dict[str, Any]) -> None:
        return None

    async def delete_retry_policy(self, task_name: str) -> None:
        return None

    async def list_retry_policies(self) -> dict[str, dict[str, Any]]:
        return {}

    async def delete_dead_letter(self, task_id: str) -> None:
        return None

    async def worker_heartbeats(self) -> dict[str, Any]:
        return {}

    async def requeue_dead_letter(self, task_id: str) -> dict[str, Any] | None:
        return None

    async def get_dead_letter(self, task_id: str) -> dict[str, Any] | None:
        return None

    async def purge_dead_letters(self) -> int:
        return 0

    async def purge_dead_letters_older_than(self, age: timedelta) -> int:
        return 0


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
                    metadata: dict[str, Any] | None = None,
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
                        "metadata": metadata or {},
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
                    recent_failures: dict[str, list[datetime]] = {}
                    auto_requeue_counts: dict[str, int] = {}
                    last_alert_sent: dict[str, datetime] = {}

                    while self.running:
                        auto_errors = set(settings.task_queue_auto_requeue_errors)
                        alert_threshold = settings.task_queue_alert_threshold
                        alert_window = timedelta(minutes=settings.task_queue_alert_window_minutes)
                        alert_channel = settings.task_queue_alert_channel or settings.slack_status_channel
                        cooldown = timedelta(minutes=settings.task_queue_alert_cooldown_minutes)

                        def _should_auto_requeue(err_type: str | None) -> bool:
                            if not err_type:
                                return False
                            return err_type in auto_errors

                        def _record_failure(err_type: str | None, task_identifier: str) -> bool:
                            if not err_type:
                                return False
                            now = utc_now()
                            key = f"{err_type}:{task_identifier}"
                            entries = recent_failures.setdefault(key, [])
                            entries.append(now)
                            cutoff = now - alert_window
                            recent_failures[key] = [ts for ts in entries if ts >= cutoff]
                            return len(recent_failures[key]) >= alert_threshold

                        async def _send_alert(error_type: str, payload: dict[str, Any]) -> None:
                            if not alert_channel:
                                return
                            if settings.dry_run or not slack_client.enabled:
                                logger.warning(
                                    "Slack alert skipped (dry run): %s", error_type
                                )
                                return
                            now = utc_now()
                            last_sent = last_alert_sent.get(error_type)
                            if last_sent and now - last_sent < cooldown:
                                return
                            body = (
                                f":rotating_light: Dead-letter threshold exceeded\n"
                                f"Queue: `{self.queue_name}`\n"
                                f"Error: `{error_type}`\n"
                                f"Task: `{payload.get('name', 'unknown')}`"
                            )
                            try:
                                await slack_client.post_digest(body, alert_channel)
                                dead_letter_alert_total.labels(queue=self.queue_name, error_type=error_type).inc()
                                last_alert_sent[error_type] = now
                            except Exception as exc:  # pragma: no cover - logging
                                logger.error("Failed to send Slack alert: %s", exc)

                        payload = await self.pop()
                        if not payload:
                            await asyncio.sleep(self._poll_interval)
                            continue

                        task_id = payload.get("task_id", "unknown")
                        name = payload.get("name")
                        coro_fn = self._registry.get(name)
                        if coro_fn is None:
                            logger.error("No registered task callable for %s", name)
                            payload.setdefault("metadata", {})
                            payload["error_type"] = "MissingCallable"
                            payload["error_message"] = f"Task callable not registered: {name}"
                            payload["stack_trace"] = None
                            payload["worker_id"] = worker_id
                            await record_dead_letter(self._redis, payload)
                            dead_letter_recorded_total.labels(
                                queue=self.queue_name,
                                error_type=payload.get("error_type", "MissingCallable"),
                            ).inc()
                            record_task_completion(self.queue_name, TaskStatus.FAILED.value)
                            continue

                        retries = payload.get("retry_count", 0)
                        base_max_retries = payload.get("max_retries", 3)

                        policy = await get_retry_policy(self._redis, name) or {}
                        timeout = float(policy.get("timeout", self._task_timeout))
                        max_retries = int(policy.get("max_retries", base_max_retries))

                        start = utc_now()
                        try:
                            result = await asyncio.wait_for(
                                coro_fn(*payload.get("args", ()), **payload.get("kwargs", {})),
                                timeout=timeout,
                            )
                        except TimeoutError:
                            payload.setdefault("metadata", {})
                            payload["retry_count"] = retries + 1
                            payload["last_error"] = "timeout"
                            payload["error_type"] = "TimeoutError"
                            payload["error_message"] = (
                                f"Task execution exceeded timeout of {self._task_timeout} seconds"
                            )
                            payload["stack_trace"] = None
                            payload["worker_id"] = worker_id
                            await record_dead_letter(self._redis, payload)
                            dead_letter_recorded_total.labels(
                                queue=self.queue_name,
                                error_type="TimeoutError",
                            ).inc()
                            record_task_completion(self.queue_name, TaskStatus.FAILED.value)
                            record_task_latency(self.queue_name, (utc_now() - start).total_seconds())
                            continue
                        except Exception as exc:  # pylint: disable=broad-except
                            payload.setdefault("metadata", {})
                            retries += 1
                            payload["retry_count"] = retries
                            payload["last_error"] = str(exc)
                            if retries >= max_retries:
                                payload["error_type"] = exc.__class__.__name__
                                payload["error_message"] = str(exc)
                                payload["stack_trace"] = traceback.format_exc()
                                payload["worker_id"] = worker_id
                                error_type = payload.get("error_type", "unknown")
                                await record_dead_letter(self._redis, payload)
                                dead_letter_recorded_total.labels(
                                    queue=self.queue_name,
                                    error_type=error_type,
                                ).inc()
                                record_task_completion(self.queue_name, TaskStatus.FAILED.value)
                                record_task_latency(self.queue_name, (utc_now() - start).total_seconds())
                                identifier = payload.get("metadata", {}).get("workflow_id") or payload.get("name", "unknown")
                                if _should_auto_requeue(error_type):
                                    key = f"{identifier}:{error_type}"
                                    count = auto_requeue_counts.get(key, 0)
                                    if count < settings.task_queue_max_auto_requeues:
                                        auto_payload = await self.requeue_dead_letter(
                                            task_id,
                                            automatic=True,
                                            notify=False,
                                        )
                                        auto_requeue_counts[key] = count + 1
                                        if auto_payload:
                                            payload = auto_payload
                                if _record_failure(error_type, identifier):
                                    await _send_alert(error_type, payload)
                                continue

                            backoff = min(
                                float(policy.get("backoff_base", self._backoff_base)) ** retries,
                                float(policy.get("backoff_max", self._backoff_max)),
                            )
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
                        await write_heartbeat(
                            self._redis, f"worker:{worker_id}", heartbeat_payload, self._heartbeat_ttl
                        )

                    logger.info("Redis worker %d stopped", worker_id)

                async def list_dead_letters(
                    self,
                    limit: int = 100,
                    offset: int = 0,
                    workflow_id: str | None = None,
                    error_type: str | None = None,
                ) -> tuple[list[dict[str, Any]], int]:
                    items, total_raw = await fetch_dead_letters(self._redis, limit=None, include_total=True)
                    filtered: list[dict[str, Any]] = []
                    for item in items:
                        if workflow_id and item.get("metadata", {}).get("workflow_id") != workflow_id:
                            continue
                        if error_type and item.get("error_type") != error_type:
                            continue
                        filtered.append(item)
                    total_filtered = len(filtered)
                    window = filtered[offset : offset + limit]
                    dead_letter_active_gauge.labels(queue=self.queue_name).set(total_filtered)
                    return window, total_filtered

                async def list_dead_letter_audit(self, limit: int = 100) -> list[dict[str, Any]]:
                    return await fetch_dead_letter_audit(self._redis, limit)

                async def get_retry_policy(self, task_name: str) -> dict[str, Any] | None:
                    return await get_retry_policy(self._redis, task_name)

                async def set_retry_policy(self, task_name: str, policy: dict[str, Any]) -> None:
                    await set_retry_policy(self._redis, task_name, policy)

                async def delete_retry_policy(self, task_name: str) -> None:
                    await delete_retry_policy(self._redis, task_name)

                async def list_retry_policies(self) -> dict[str, dict[str, Any]]:
                    return await list_retry_policies(self._redis)

                async def delete_dead_letter(self, task_id: str) -> None:
                    await clear_dead_letter(self._redis, task_id)

                async def worker_heartbeats(self) -> dict[str, dict[str, Any]]:
                    return await list_heartbeats(self._redis)

                async def requeue_dead_letter(
                    self,
                    task_id: str,
                    *,
                    automatic: bool = False,
                    notify: bool = True,
                ) -> dict[str, Any] | None:
                    payload = await get_dead_letter(self._redis, task_id)
                    if payload is None:
                        return None

                    await clear_dead_letter(self._redis, task_id)
                    payload.pop("last_error", None)
                    payload["retry_count"] = 0
                    payload["requeued_at"] = utc_now().isoformat()
                    await redis_enqueue_task(self._redis, payload.get("name", "unknown"), payload)
                    record_task_enqueued(self.queue_name)
                    metric = dead_letter_auto_requeue_total if automatic else dead_letter_requeued_total
                    metric.labels(queue=self.queue_name, error_type=payload.get("error_type", "unknown")).inc()
                    logger.info("Dead-letter task requeued: %s", task_id)
                    return payload

                async def get_dead_letter(self, task_id: str) -> dict[str, Any] | None:
                    return await get_dead_letter(self._redis, task_id)

                async def purge_dead_letters(self, *, older_than: timedelta | None = None) -> int:
                    if older_than is None:
                        deleted = await purge_dead_letters(self._redis)
                        dead_letter_purged_total.labels(queue=self.queue_name, mode="all").inc(deleted)
                        dead_letter_active_gauge.labels(queue=self.queue_name).set(await count_dead_letters(self._redis))
                        return deleted
                    cutoff = utc_now() - older_than
                    deleted = await purge_dead_letters(self._redis, older_than=cutoff)
                    dead_letter_purged_total.labels(queue=self.queue_name, mode="age_filter").inc(deleted)
                    dead_letter_active_gauge.labels(queue=self.queue_name).set(await count_dead_letters(self._redis))
                    return deleted

            _task_queue = RedisTaskQueue(max_workers=settings.task_queue_workers)
        else:
            _task_queue = TaskQueue(max_workers=settings.task_queue_workers)
            await _task_queue.start()
    return _task_queue
