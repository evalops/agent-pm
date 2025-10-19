import json

import pytest

from agent_pm.storage import redis


class DummyRedis:
    def __init__(self):
        self.items: list[str] = []
        self.results: dict[str, str] = {}

    async def rpush(self, key: str, value: str) -> None:
        self.items.append(value)

    async def lpop(self, key: str):
        if not self.items:
            return None
        return self.items.pop(0)

    async def hset(self, key: str, field: str, value: str) -> None:
        self.results[field] = value

    async def hget(self, key: str, field: str):
        return self.results.get(field)


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
