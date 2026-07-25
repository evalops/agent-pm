"""Guardrail helpers for approvals, dry-run enforcement, and rate limiting."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from ..settings import settings

logger = logging.getLogger(__name__)


@dataclass
class GuardrailContext:
    approved: bool = False
    dry_run: bool = settings.dry_run


guardrail_context = GuardrailContext()


@asynccontextmanager
async def rate_limited(lock: asyncio.Lock) -> AsyncIterator[None]:
    await lock.acquire()
    try:
        yield
    finally:
        lock.release()


__all__ = ["guardrail_context", "rate_limited"]
