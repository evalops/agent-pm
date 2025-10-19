"""Redis-backed task queue using redis.asyncio."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as redis

from agent_pm.settings import settings

logger = logging.getLogger(__name__)

QUEUE_KEY = "agent_pm:tasks"
RESULT_KEY = "agent_pm:results"
DEAD_LETTER_KEY = "agent_pm:dead_letter"
HEARTBEAT_KEY = "agent_pm:worker_heartbeats"


def _queue_key() -> str:
    return QUEUE_KEY


def _result_key() -> str:
    return RESULT_KEY


def _dead_letter_key() -> str:
    return DEAD_LETTER_KEY


def _heartbeat_key() -> str:
    return HEARTBEAT_KEY


async def get_redis_client() -> redis.Redis:
    return redis.from_url(settings.redis_url, decode_responses=True)


async def enqueue_task(
    client: redis.Redis,
    name: str,
    payload: dict[str, Any],
) -> str:
    task_id = payload.setdefault("task_id", uuid.uuid4().hex)
    payload.setdefault("name", name)
    await client.rpush(_queue_key(), json.dumps(payload))
    logger.info("Queued redis task %s (%s)", name, task_id)
    return task_id


async def pop_task(client: redis.Redis) -> dict[str, Any] | None:
    item = await client.lpop(_queue_key())
    if not item:
        return None
    return json.loads(item)


async def set_task_result(client: redis.Redis, task_id: str, result: dict[str, Any]) -> None:
    await client.hset(_result_key(), task_id, json.dumps(result))


async def get_task_result(client: redis.Redis, task_id: str) -> dict[str, Any] | None:
    item = await client.hget(_result_key(), task_id)
    if not item:
        return None
    return json.loads(item)


async def record_dead_letter(client: redis.Redis, payload: dict[str, Any]) -> None:
    task_id = payload.get("task_id", uuid.uuid4().hex)
    if "recorded_at" not in payload:
        payload["recorded_at"] = datetime.now(timezone.utc).isoformat()
    await client.hset(_dead_letter_key(), task_id, json.dumps(payload))


async def get_dead_letter(client: redis.Redis, task_id: str) -> dict[str, Any] | None:
    item = await client.hget(_dead_letter_key(), task_id)
    if not item:
        return None
    try:
        data = json.loads(item)
    except json.JSONDecodeError:
        return None
    data.setdefault("task_id", task_id)
    return data


async def fetch_dead_letters(client: redis.Redis, limit: int = 100) -> list[dict[str, Any]]:
    items = await client.hgetall(_dead_letter_key())
    tasks: list[dict[str, Any]] = []
    for task_id, value in items.items():
        if len(tasks) >= limit:
            break
        try:
            data = json.loads(value)
            data.setdefault("task_id", task_id)
            tasks.append(data)
        except json.JSONDecodeError:
            tasks.append({"task_id": task_id, "raw": value})
    return tasks


async def clear_dead_letter(client: redis.Redis, task_id: str) -> None:
    await client.hdel(_dead_letter_key(), task_id)


async def purge_dead_letters(client: redis.Redis, *, older_than: datetime | None = None) -> int:
    if older_than is None:
        count = await client.hlen(_dead_letter_key())
        if count == 0:
            return 0
        await client.delete(_dead_letter_key())
        return int(count)

    items = await client.hgetall(_dead_letter_key())
    removed = 0
    for task_id, value in items.items():
        try:
            data = json.loads(value)
            recorded = data.get("recorded_at")
            if not recorded:
                continue
            recorded_dt = datetime.fromisoformat(recorded)
        except (json.JSONDecodeError, ValueError):
            continue
        if recorded_dt <= older_than:
            await client.hdel(_dead_letter_key(), task_id)
            removed += 1
    return removed


async def write_heartbeat(client: redis.Redis, worker_id: str, payload: dict[str, Any], ttl: int) -> None:
    await client.hset(_heartbeat_key(), worker_id, json.dumps(payload))
    await client.expire(_heartbeat_key(), ttl)


async def list_heartbeats(client: redis.Redis) -> dict[str, dict[str, Any]]:
    items = await client.hgetall(_heartbeat_key())
    heartbeats: dict[str, dict[str, Any]] = {}
    for worker_id, value in items.items():
        try:
            heartbeats[worker_id] = json.loads(value)
        except json.JSONDecodeError:
            heartbeats[worker_id] = {"raw": value}
    return heartbeats
