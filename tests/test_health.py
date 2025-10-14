"""Tests for health check utilities."""

import pytest

from agent_pm.health import check_agents_config, check_all_dependencies, check_session_db, check_trace_dir


@pytest.mark.asyncio
async def test_check_session_db():
    result = await check_session_db()
    assert result["status"] in ("ok", "error")
    assert result["service"] == "session_db"


@pytest.mark.asyncio
async def test_check_trace_dir():
    result = await check_trace_dir()
    assert result["status"] in ("ok", "error")
    assert result["service"] == "trace_dir"


@pytest.mark.asyncio
async def test_check_agents_config(tmp_path):
    # Should handle missing config gracefully
    result = await check_agents_config()
    assert result["service"] == "agents_config"
    # Either ok if file exists, warn if not found, error if malformed
    assert result["status"] in ("ok", "warn", "error")


@pytest.mark.asyncio
async def test_check_all_dependencies():
    result = await check_all_dependencies()
    assert "status" in result
    assert "checks" in result
    assert isinstance(result["checks"], list)
    # At least one check should have run
    assert len(result["checks"]) >= 4
