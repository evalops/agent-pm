"""Redis-backed distributed task queue using ARQ."""

from __future__ import annotations

import logging
from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from agent_pm.settings import settings

logger = logging.getLogger(__name__)


async def get_redis_pool() -> ArqRedis:
    """Get or create Redis connection pool for ARQ."""
    # Parse Redis URL
    url = settings.redis_url
    if url.startswith("redis://"):
        host_port = url.replace("redis://", "").split("/")[0]
        host, port = host_port.split(":") if ":" in host_port else (host_port, 6379)
    else:
        host, port = "localhost", 6379

    redis_settings = RedisSettings(host=host, port=int(port))
    return await create_pool(redis_settings)


async def enqueue_task(
    pool: ArqRedis,
    function_name: str,
    *args: Any,
    **kwargs: Any,
) -> str:
    """Enqueue a task to Redis queue.

    Returns:
        Job ID
    """
    job = await pool.enqueue_job(function_name, *args, **kwargs)
    logger.info("Enqueued task: %s (job_id=%s)", function_name, job.job_id)
    return job.job_id


async def get_job_status(pool: ArqRedis, job_id: str) -> dict[str, Any] | None:
    """Get status of a job by ID."""
    job_result = await pool.job_status(job_id)
    if not job_result:
        return None

    return {
        "job_id": job_id,
        "status": job_result.job_status.value if job_result.job_status else "unknown",
        "result": job_result.result,
        "start_time": job_result.start_time.isoformat() if job_result.start_time else None,
        "finish_time": job_result.finish_time.isoformat() if job_result.finish_time else None,
    }


# ARQ worker functions (to be imported by worker process)
async def example_background_task(ctx: dict, plan_id: str, user: str) -> dict:
    """Example background task function."""
    logger.info("Processing background task for plan_id=%s user=%s", plan_id, user)
    # Perform async work here
    return {"plan_id": plan_id, "status": "processed"}


# Worker class for ARQ
class WorkerSettings:
    """ARQ worker configuration."""

    functions = [example_background_task]
    redis_settings = RedisSettings(host="localhost", port=6379)
    max_jobs = 10
    job_timeout = 300  # 5 minutes
