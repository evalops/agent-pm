import asyncio

import pytest

from agent_pm.storage import redis


class FakeJobResult:
    def __init__(self, job_id: str, status: str):
        self.job_status = type("JobStatus", (), {"value": status})()
        self.result = {"ok": True}
        self.start_time = None
        self.finish_time = None


class FakeRedisPool:
    def __init__(self):
        self.jobs: dict[str, tuple[str, tuple, dict]] = {}

    async def enqueue_job(self, function_name, *args, **kwargs):
        job_id = f"job-{len(self.jobs) + 1}"
        self.jobs[job_id] = (function_name, args, kwargs)
        return type("Job", (), {"job_id": job_id})()

    async def job_status(self, job_id):
        if job_id not in self.jobs:
            return None
        return FakeJobResult(job_id, "complete")


@pytest.mark.asyncio
async def test_enqueue_task_records_job(monkeypatch):
    pool = FakeRedisPool()

    job_id = await redis.enqueue_task(pool, "do_work", 1, foo="bar")

    assert job_id in pool.jobs
    function_name, args, kwargs = pool.jobs[job_id]
    assert function_name == "do_work"
    assert args == (1,)
    assert kwargs == {"foo": "bar"}


@pytest.mark.asyncio
async def test_get_job_status_returns_dict():
    pool = FakeRedisPool()
    job_id = await redis.enqueue_task(pool, "do_work")

    status = await redis.get_job_status(pool, job_id)
    assert status is not None
    assert status["job_id"] == job_id
    assert status["status"] == "complete"
    assert status["result"] == {"ok": True}
