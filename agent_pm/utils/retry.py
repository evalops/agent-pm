"""Async retry utilities for HTTP operations."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)


async def with_exponential_backoff(
    operation: Callable[[], Awaitable[T]],
    attempts: int = 3,
    base_delay: float = 0.5,
    jitter: float = 0.1,
) -> T:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except Exception as exc:  # pragma: no cover - retry guard
            last_error = exc
            if attempt == attempts:
                break
            sleep_for = base_delay * (2 ** (attempt - 1)) + random.uniform(0, jitter)
            logger.warning(
                "Retryable error (attempt %s/%s): %s", attempt, attempts, exc
            )
            await asyncio.sleep(sleep_for)
    assert last_error is not None
    raise last_error


__all__ = ["with_exponential_backoff"]
