"""Utilities for working with OpenAI clients."""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI

from .settings import settings


def get_async_openai_client(*, timeout: float | None = None) -> AsyncOpenAI | None:
    """Build an ``AsyncOpenAI`` client or return ``None`` in dry-run mode."""

    api_key = settings.openai_api_key
    if not api_key:
        if settings.dry_run:
            return None
        raise RuntimeError("OPENAI_API_KEY is required for OpenAI access")

    client_kwargs: dict[str, Any] = {"api_key": api_key}
    if timeout is not None:
        client_kwargs["timeout"] = timeout
    return AsyncOpenAI(**client_kwargs)


__all__ = ["get_async_openai_client"]
