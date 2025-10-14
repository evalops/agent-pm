"""Tests for API authentication."""

import pytest
from fastapi import HTTPException

from agent_pm.auth import verify_api_key


@pytest.mark.asyncio
async def test_verify_api_key_no_key_configured(monkeypatch):
    """When no API key is configured, auth should be disabled."""
    from agent_pm.settings import Settings

    monkeypatch.setattr("agent_pm.auth.settings", Settings(OPENAI_API_KEY="test", API_KEY=None))  # type: ignore
    result = await verify_api_key(None)
    assert result == "anonymous"


@pytest.mark.asyncio
async def test_verify_api_key_missing():
    """Missing API key when auth is enabled should raise 401."""
    from agent_pm import auth
    from agent_pm.settings import Settings

    auth.settings = Settings(OPENAI_API_KEY="test", API_KEY="secret123")  # type: ignore
    with pytest.raises(HTTPException) as exc_info:
        await verify_api_key(None)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_api_key_invalid():
    """Invalid API key should raise 403."""
    from agent_pm import auth
    from agent_pm.settings import Settings

    auth.settings = Settings(OPENAI_API_KEY="test", API_KEY="secret123")  # type: ignore
    with pytest.raises(HTTPException) as exc_info:
        await verify_api_key("wrong_key")
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_verify_api_key_valid():
    """Valid API key should pass."""
    from agent_pm import auth
    from agent_pm.settings import Settings

    auth.settings = Settings(OPENAI_API_KEY="test", API_KEY="secret123")  # type: ignore
    result = await verify_api_key("secret123")
    assert result == "secret123"
