"""API key authentication middleware for Agent PM."""

from __future__ import annotations

import logging
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader

from ..settings import settings

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str | None = Security(api_key_header)) -> str:
    """Verify API key from X-API-Key header."""
    expected_key = settings.api_key
    if not expected_key:
        # Auth disabled when no API key configured
        return "anonymous"

    if not api_key:
        logger.warning("Missing API key in request")
        raise HTTPException(status_code=401, detail="Missing API key")

    if not secrets.compare_digest(api_key, expected_key):
        logger.warning("Invalid API key attempted")
        raise HTTPException(status_code=403, detail="Invalid API key")

    return api_key


# FastAPI dependency for protected routes
APIKeyDep = Annotated[str, Depends(verify_api_key)]


async def verify_admin_key(api_key: str | None = Security(api_key_header)) -> str:
    """Verify admin API key (stricter check for admin operations)."""
    expected_admin_key = settings.admin_api_key
    if not expected_admin_key:
        # Fall back to regular API key if no admin key configured
        return await verify_api_key(api_key)

    if not api_key:
        logger.warning("Missing admin API key in request")
        raise HTTPException(status_code=401, detail="Missing admin API key")

    if not secrets.compare_digest(api_key, expected_admin_key):
        logger.warning("Invalid admin API key attempted")
        raise HTTPException(status_code=403, detail="Invalid admin API key")

    return api_key


# FastAPI dependency for admin routes
AdminKeyDep = Annotated[str, Depends(verify_admin_key)]
