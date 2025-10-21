import json
from datetime import UTC, datetime, timedelta

import pytest

from agent_pm.storage import redis


class DummyRedis:
    def __init__(self):
        self.items: list[str] = []
        self.results: dict[str, str] = {}
        self.dead: dict[str, str] = {}
        self.heartbeats: dict[str, str] = {}
        self.audit: list[str] = []
        self.retry_policies: dict[str, str] = {}

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
        elif key.endswith("retry_policy"):
            self.retry_policies[field] = value
        else:
            self.results[field] = value

    async def hget(self, key: str, field: str):
        if key.endswith("dead_letter"):
            return self.dead.get(field)
        if key.endswith("retry_policy"):
            return self.retry_policies.get(field)
        return self.results.get(field)

    async def hgetall(self, key: str):
        if key.endswith("dead_letter"):
            return self.dead
        if key.endswith("heartbeats"):
            return self.heartbeats
        if key.endswith("retry_policy"):
            return self.retry_policies
        return self.results

    async def hdel(self, key: str, field: str):
        if key.endswith("dead_letter"):
            self.dead.pop(field, None)
        elif key.endswith("retry_policy"):
            self.retry_policies.pop(field, None)

    async def hlen(self, key: str) -> int:
        if key.endswith("dead_letter"):
            return len(self.dead)
        if key.endswith("retry_policy"):
            return len(self.retry_policies)
        return len(self.results)

    async def expire(self, key: str, ttl: int):
        return None

    async def delete(self, key: str):
        if key.endswith("dead_letter"):
            self.dead.clear()

    async def lpush(self, key: str, value: str):
        if key.endswith("dead_letter:audit"):
            self.audit.insert(0, value)

    async def ltrim(self, key: str, start: int, stop: int):
        if key.endswith("dead_letter:audit"):
            self.audit = self.audit[start : stop + 1]

    async def lrange(self, key: str, start: int, stop: int):
        if key.endswith("dead_letter:audit"):
            return self.audit[start : stop + 1]
        return []


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
    assert isinstance(records, list)
    assert records[0]["task_id"] == "abc"

    stored = await redis.get_dead_letter(client, "abc")
    assert stored["task_id"] == "abc"

    await redis.clear_dead_letter(client, "abc")
    assert await redis.fetch_dead_letters(client) == []

    old_payload = {
        "task_id": "old",
        "name": "job",
        "args": [],
        "kwargs": {},
        "recorded_at": "2000-01-01T00:00:00+00:00",
    }
    await redis.record_dead_letter(client, old_payload)
    removed = await redis.purge_dead_letters(client)
    assert removed == 1
    assert await redis.fetch_dead_letters(client) == []

    # ensure age-based purging skips fresh entries
    await redis.record_dead_letter(client, payload)
    removed = await redis.purge_dead_letters(client, older_than=datetime.now(UTC) - timedelta(minutes=1))
    assert removed == 0

    await redis.write_heartbeat(client, "worker:1", {"status": "ok"}, ttl=60)
    beats = await redis.list_heartbeats(client)
    assert beats["worker:1"]["status"] == "ok"

    await redis.append_dead_letter_audit(client, {"event": "record", "task_id": "abc"}, max_entries=10)
    audit_entries = await redis.fetch_dead_letter_audit(client, limit=1)
    assert audit_entries[0]["task_id"] == "abc"

    await redis.set_retry_policy(client, "job", {"timeout": 123, "backoff_base": 2.5})
    policy = await redis.get_retry_policy(client, "job")
    assert policy == {"timeout": 123, "backoff_base": 2.5}
    policies = await redis.list_retry_policies(client)
    assert "job" in policies
    await redis.delete_retry_policy(client, "job")
    assert await redis.get_retry_policy(client, "job") is None
