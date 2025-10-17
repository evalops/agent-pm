"""Agent PM package initialisation."""

from __future__ import annotations

from .settings import settings

try:
    from agents import set_tracing_disabled
except ImportError:  # pragma: no cover - agents package not installed
    set_tracing_disabled = None
else:
    should_disable_tracing = settings.dry_run or not settings.openai_api_key
    if set_tracing_disabled and should_disable_tracing:
        set_tracing_disabled(True)

__all__ = ["settings"]
