"""Redis-backed task queue using redis.asyncio."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import redis.asyncio as redis

from agent_pm.settings import settings

logger = logging.getLogger(__name__)

QUEUE_KEY = "agent_pm:tasks"
RESULT_KEY = "agent_pm:results"


def _queue_key() -> str:
    return QUEUE_KEY


def _result_key() -> str:
    return RESULT_KEY


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
