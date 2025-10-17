"""Health check utilities for Agent PM dependencies."""

from __future__ import annotations

import logging
from typing import Any

import yaml

from agent_pm.openai_utils import get_async_openai_client
from agent_pm.settings import settings

logger = logging.getLogger(__name__)


async def check_openai() -> dict[str, Any]:
    """Verify OpenAI API connectivity, reflecting dry-run mode when no key is configured."""
    client = get_async_openai_client(timeout=5.0)
    if client is None:
        detail = "OPENAI_API_KEY not configured"
        status = "warn" if settings.dry_run else "error"
        return {"status": status, "service": "openai", "detail": detail}

    try:
        await client.models.list()
        return {"status": "ok", "service": "openai"}
    except Exception as exc:
        logger.warning("OpenAI health check failed: %s", exc)
        return {"status": "error", "service": "openai", "detail": str(exc)}


async def check_session_db() -> dict[str, Any]:
    """Verify agent session DB is writable."""
    try:
        db_path = settings.agent_session_db
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # Minimal check: ensure parent dir exists and is writable
        test_file = db_path.parent / ".health_check_tmp"
        test_file.touch()
        test_file.unlink()
        return {"status": "ok", "service": "session_db", "path": str(db_path)}
    except Exception as exc:
        logger.warning("Session DB health check failed: %s", exc)
        return {"status": "error", "service": "session_db", "detail": str(exc)}


async def check_agents_config() -> dict[str, Any]:
    """Verify agent configuration is valid YAML."""
    try:
        cfg_path = settings.agents_config_path
        if not cfg_path.exists():
            return {
                "status": "warn",
                "service": "agents_config",
                "detail": "config not found",
            }
        with open(cfg_path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict) or (
            "planner" not in data and "critic" not in data
        ):
            return {
                "status": "error",
                "service": "agents_config",
                "detail": "invalid structure",
            }
        return {
            "status": "ok",
            "service": "agents_config",
            "profiles": list(data.keys()),
        }
    except Exception as exc:
        logger.warning("Agents config health check failed: %s", exc)
        return {"status": "error", "service": "agents_config", "detail": str(exc)}


async def check_trace_dir() -> dict[str, Any]:
    """Verify trace directory is writable."""
    try:
        trace_dir = settings.trace_dir
        trace_dir.mkdir(parents=True, exist_ok=True)
        test_file = trace_dir / ".health_check_tmp"
        test_file.touch()
        test_file.unlink()
        return {"status": "ok", "service": "trace_dir", "path": str(trace_dir)}
    except Exception as exc:
        logger.warning("Trace dir health check failed: %s", exc)
        return {"status": "error", "service": "trace_dir", "detail": str(exc)}


async def check_all_dependencies() -> dict[str, Any]:
    """Run all health checks in parallel."""
    import asyncio

    results = await asyncio.gather(
        check_openai(),
        check_session_db(),
        check_agents_config(),
        check_trace_dir(),
        return_exceptions=True,
    )
    checks = []
    overall_status = "ok"
    for r in results:
        if isinstance(r, Exception):
            checks.append({"status": "error", "detail": str(r)})
            overall_status = "error"
        else:
            checks.append(r)
            if r["status"] == "error":
                overall_status = "error"
            elif r["status"] == "warn" and overall_status == "ok":
                overall_status = "warn"
    return {"status": overall_status, "checks": checks}
