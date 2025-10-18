"""Rate limiting and backpressure controls for Agent PM."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from typing import Annotated

from fastapi import Depends, HTTPException, Request

logger = logging.getLogger(__name__)


@dataclass
class RateLimitBucket:
    """Token bucket for rate limiting."""

    capacity: int
    refill_rate: float  # tokens per second
    tokens: float
    last_refill: float

    def consume(self, tokens: int = 1) -> bool:
        """Attempt to consume tokens. Returns True if allowed."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False


class RateLimiter:
    """Per-IP token bucket rate limiter."""

    def __init__(self, capacity: int = 10, refill_rate: float = 1.0):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.buckets: dict[str, RateLimitBucket] = {}
        self._lock = asyncio.Lock()

    async def check(self, client_ip: str) -> None:
        """Raise 429 if rate limit exceeded."""
        async with self._lock:
            if client_ip not in self.buckets:
                self.buckets[client_ip] = RateLimitBucket(
                    capacity=self.capacity,
                    refill_rate=self.refill_rate,
                    tokens=float(self.capacity),
                    last_refill=time.monotonic(),
                )
            bucket = self.buckets[client_ip]
            if not bucket.consume():
                logger.warning("Rate limit exceeded for IP: %s", client_ip)
                raise HTTPException(status_code=429, detail="Rate limit exceeded")


class ConcurrencyLimiter:
    """Global semaphore for concurrent request limiting."""

    def __init__(self, max_concurrent: int = 10):
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def acquire(self) -> None:
        """Acquire semaphore; blocks if at capacity."""
        acquired = await self.semaphore.acquire()
        if not acquired:
            raise HTTPException(status_code=503, detail="Service at capacity")

    def release(self) -> None:
        """Release semaphore."""
        self.semaphore.release()


# Global instances
_rate_limiter = RateLimiter(capacity=20, refill_rate=2.0)  # 20 req burst, 2 req/sec refill
_concurrency_limiter = ConcurrencyLimiter(max_concurrent=10)


async def enforce_rate_limit(request: Request) -> None:
    """Dependency for FastAPI routes to enforce per-IP rate limit."""
    client_ip = request.client.host if request.client else "unknown"
    await _rate_limiter.check(client_ip)


async def enforce_concurrency_limit() -> None:
    """Dependency to enforce global concurrency limit."""
    await _concurrency_limiter.acquire()


def release_concurrency() -> None:
    """Release global concurrency slot."""
    _concurrency_limiter.release()


RateLimitDep = Annotated[None, Depends(enforce_rate_limit)]
ConcurrencyLimitDep = Annotated[None, Depends(enforce_concurrency_limit)]
