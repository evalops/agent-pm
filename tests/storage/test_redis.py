import json
from datetime import datetime, timedelta, timezone

import pytest

from agent_pm.storage import redis


class DummyRedis:
    def __init__(self):
        self.items: list[str] = []
        self.results: dict[str, str] = {}
        self.dead: dict[str, str] = {}
        self.heartbeats: dict[str, str] = {}

    async def rpush(self, key: str, value: str) -> None:
        self.items.append(value)

    async def lpop(self, key: str):
        if not self.items:
            return None
        return self.items.pop(0)

    async def llen(self, key: str) -> int:
        return len(self.items)

    async def hset(self, key: str, field: str, value: str) -> None:
        if key.endswith("dead_letter"):
            self.dead[field] = value
        elif key.endswith("heartbeats"):
            self.heartbeats[field] = value
        else:
            self.results[field] = value

    async def hget(self, key: str, field: str):
        if key.endswith("dead_letter"):
            return self.dead.get(field)
        return self.results.get(field)

    async def hgetall(self, key: str):
        if key.endswith("dead_letter"):
            return self.dead
        if key.endswith("heartbeats"):
            return self.heartbeats
        return self.results

    async def hdel(self, key: str, field: str):
        if key.endswith("dead_letter"):
            self.dead.pop(field, None)

    async def hlen(self, key: str) -> int:
        if key.endswith("dead_letter"):
            return len(self.dead)
        return len(self.results)

    async def expire(self, key: str, ttl: int):
        return None

    async def delete(self, key: str):
        if key.endswith("dead_letter"):
            self.dead.clear()


@pytest.mark.asyncio
async def test_enqueue_task_pushes_payload():
    client = DummyRedis()
    payload = {"args": [1], "kwargs": {"foo": "bar"}}

    task_id = await redis.enqueue_task(client, "do_work", payload)

    assert isinstance(task_id, str)
    stored = json.loads(client.items[0])
    assert stored["name"] == "do_work"
    assert stored["args"] == [1]
    assert stored["kwargs"] == {"foo": "bar"}


@pytest.mark.asyncio
async def test_pop_and_result_roundtrip():
    client = DummyRedis()
    payload = {"args": [], "kwargs": {}}
    task_id = await redis.enqueue_task(client, "noop", payload)

    popped = await redis.pop_task(client)
    assert popped is not None
    assert popped["task_id"] == task_id

    await redis.set_task_result(client, task_id, {"status": "done"})
    result = await redis.get_task_result(client, task_id)
    assert result == {"status": "done"}


@pytest.mark.asyncio
async def test_dead_letter_and_heartbeat_helpers():
    client = DummyRedis()
    payload = {"task_id": "abc", "name": "job", "args": [], "kwargs": {}}
    await redis.record_dead_letter(client, payload)
    records = await redis.fetch_dead_letters(client)
    assert records[0]["task_id"] == "abc"

    stored = await redis.get_dead_letter(client, "abc")
    assert stored["task_id"] == "abc"

    await redis.clear_dead_letter(client, "abc")
    assert await redis.fetch_dead_letters(client) == []

    old_payload = {"task_id": "old", "name": "job", "args": [], "kwargs": {}, "recorded_at": "2000-01-01T00:00:00+00:00"}
    await redis.record_dead_letter(client, old_payload)
    removed = await redis.purge_dead_letters(client)
    assert removed == 1
    assert await redis.fetch_dead_letters(client) == []

    # ensure age-based purging skips fresh entries
    await redis.record_dead_letter(client, payload)
    removed = await redis.purge_dead_letters(client, older_than=datetime.now(timezone.utc) - timedelta(minutes=1))
    assert removed == 0

    await redis.write_heartbeat(client, "worker:1", {"status": "ok"}, ttl=60)
    beats = await redis.list_heartbeats(client)
    assert beats["worker:1"]["status"] == "ok"
