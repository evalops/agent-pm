"""Guardrail helpers for approvals, dry-run enforcement, and rate limiting."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from ..settings import settings

logger = logging.getLogger(__name__)


@dataclass
class GuardrailContext:
    approved: bool = False
    dry_run: bool = settings.dry_run

    def require_approval(self) -> None:
        if settings.approval_required and not self.approved:
            raise PermissionError("Operation blocked: approval required.")


guardrail_context = GuardrailContext()


def approval_granted() -> None:
    guardrail_context.approved = True
    logger.info("[guardrail] Approval granted")


@asynccontextmanager
async def rate_limited(lock: asyncio.Lock) -> AsyncIterator[None]:
    await lock.acquire()
    try:
        yield
    finally:
        lock.release()


def dry_run_action(action: Callable[[], str], description: str) -> str | None:
    if guardrail_context.dry_run:
        logger.info("[guardrail] Dry-run: %s", description)
        return action()
    return None


__all__ = ["guardrail_context", "approval_granted", "rate_limited", "dry_run_action"]
