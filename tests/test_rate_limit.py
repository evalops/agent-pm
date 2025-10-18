"""Tests for rate limiting and backpressure."""

import asyncio

import pytest
from fastapi import HTTPException

from agent_pm.api.rate_limit import ConcurrencyLimiter, RateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_allows_within_capacity():
    limiter = RateLimiter(capacity=5, refill_rate=10.0)
    # Should allow first 5 requests immediately
    for _ in range(5):
        await limiter.check("test_ip")


@pytest.mark.asyncio
async def test_rate_limiter_blocks_over_capacity():
    limiter = RateLimiter(capacity=2, refill_rate=0.1)
    await limiter.check("test_ip")
    await limiter.check("test_ip")
    with pytest.raises(HTTPException) as exc_info:
        await limiter.check("test_ip")
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_rate_limiter_refills_over_time():
    limiter = RateLimiter(capacity=1, refill_rate=10.0)  # refill 1 token in 0.1s
    await limiter.check("test_ip")
    await asyncio.sleep(0.15)
    await limiter.check("test_ip")  # should succeed after refill


@pytest.mark.asyncio
async def test_concurrency_limiter():
    limiter = ConcurrencyLimiter(max_concurrent=2)
    await limiter.acquire()
    await limiter.acquire()
    # Third acquire should block, but we won't wait for it
    limiter.release()
    await limiter.acquire()  # should succeed after release
