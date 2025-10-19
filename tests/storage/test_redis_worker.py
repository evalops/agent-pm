import asyncio
from collections import deque

import pytest
import pytest_asyncio

from agent_pm.settings import settings
from agent_pm.storage import redis as redis_helpers
import agent_pm.storage.tasks as tasks_module


class InMemoryRedis:
    def __init__(self):
        self.items: deque[str] = deque()
        self.hashes: dict[str, dict[str, str]] = {}

    async def rpush(self, key: str, value: str) -> None:
        self.items.append(value)

    async def lpop(self, key: str):
        if not self.items:
            return None
        return self.items.popleft()

    async def llen(self, key: str) -> int:
        return len(self.items)

    async def hset(self, key: str, field: str, value: str) -> None:
        self.hashes.setdefault(key, {})[field] = value

    async def hget(self, key: str, field: str):
        return self.hashes.get(key, {}).get(field)

    async def hgetall(self, key: str):
        return dict(self.hashes.get(key, {}))

    async def hdel(self, key: str, field: str):
        self.hashes.setdefault(key, {}).pop(field, None)

    async def hlen(self, key: str) -> int:
        return len(self.hashes.get(key, {}))

    async def expire(self, key: str, ttl: int):
        return None

    async def flushall(self):
        self.items.clear()
        self.hashes.clear()


@pytest_asyncio.fixture
async def redis_queue(monkeypatch):
    fake = InMemoryRedis()
    monkeypatch.setattr(settings, "task_queue_backend", "redis")
    monkeypatch.setattr(settings, "task_queue_workers", 1)
    monkeypatch.setattr(settings, "task_queue_poll_interval", 0.01)
    monkeypatch.setattr(settings, "task_queue_retry_backoff_base", 1.1)
    monkeypatch.setattr(settings, "task_queue_retry_backoff_max", 0.05)
    monkeypatch.setattr(settings, "task_queue_task_timeout", 1)

    async def fake_client():
        return fake

    monkeypatch.setattr(tasks_module, "get_redis_client", fake_client, raising=False)
    tasks_module._task_queue = None
    queue = await tasks_module.get_task_queue()
    await queue.start()
    try:
        yield queue, fake
    finally:
        await queue.stop()
        await fake.flushall()
        tasks_module._task_queue = None


@pytest.mark.asyncio
async def test_redis_worker_executes_and_records_result(redis_queue):
    queue, fake = redis_queue

    async def add(x: int, y: int) -> int:
        await asyncio.sleep(0.01)
        return x + y

    task_id = await queue.enqueue("add", add, 1, 2)

    result = None
    for _ in range(50):
        result = await redis_helpers.get_task_result(fake, task_id)
        if result:
            break
        await asyncio.sleep(0.05)

    assert result is not None
    assert result["status"] == "completed"
    assert result["result"] == 3

    heartbeats = await redis_helpers.list_heartbeats(fake)
    assert heartbeats


@pytest.mark.asyncio
async def test_redis_worker_dead_letters_after_retries_exhausted(redis_queue):
    queue, fake = redis_queue

    async def fail_task() -> None:
        raise RuntimeError("boom")

    task_id = await queue.enqueue("explode", fail_task, max_retries=1)

    letters: list[dict] = []
    for _ in range(50):
        letters = await redis_helpers.fetch_dead_letters(fake)
        if letters:
            break
        await asyncio.sleep(0.05)

    assert letters
    match = next((entry for entry in letters if entry.get("task_id") == task_id), None)
    assert match is not None
    assert match["retry_count"] == 1
    assert match["last_error"] == "boom"

    requeued = await queue.requeue_dead_letter(task_id)
    assert requeued is not None
    assert requeued["task_id"] == task_id
    assert requeued["retry_count"] == 0

    queued_len = await fake.llen("agent_pm:tasks")
    assert queued_len >= 1
