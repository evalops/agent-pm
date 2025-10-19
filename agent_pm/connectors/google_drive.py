"""Google Drive connector with dry-run behaviour."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from agent_pm.connectors.base import Connector
from agent_pm.settings import settings

logger = logging.getLogger(__name__)


class GoogleDriveConnector(Connector):
    def __init__(self) -> None:
        super().__init__(name="google_drive")
        self._query = settings.google_drive_query
        self._scopes = settings.google_drive_scopes
        self._service_account_info = self._load_service_account_info()
        self._credentials = None
        self._credentials_error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._service_account_info)

    async def sync(self, *, since: datetime | None = None) -> list[dict[str, Any]]:
        query = self._build_query(since)
        if settings.dry_run or not self.enabled:
            return [{"dry_run": True, "query": query}]

        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        params = {"q": query} if query else {}
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://www.googleapis.com/drive/v3/files",
                headers=headers,
                params=params,
                timeout=30,
            )
        response.raise_for_status()
        return [response.json()]

    def _build_query(self, since: datetime | None) -> str | None:
        query = self._query or None
        if since:
            updated_clause = f"modifiedTime >= '{since.astimezone(UTC).isoformat()}'"
            query = f"{query} and {updated_clause}" if query else updated_clause
        return query

    async def _get_token(self) -> str:
        creds = await asyncio.to_thread(self._ensure_credentials)
        if not creds:
            raise RuntimeError("Google Drive credentials missing")
        if not creds.valid or creds.expired:
            await asyncio.to_thread(creds.refresh, self._request())
        if not creds.token:
            raise RuntimeError("Failed to obtain drive token")
        return creds.token

    def _load_service_account_info(self) -> dict[str, Any] | None:
        if settings.google_service_account_json:
            try:
                return json.loads(settings.google_service_account_json)
            except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                logger.error("Invalid GOOGLE_SERVICE_ACCOUNT_JSON: %s", exc)
                return None
        if settings.google_service_account_file:
            path = Path(settings.google_service_account_file)
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                logger.error("GOOGLE_SERVICE_ACCOUNT_FILE not found: %s", path)
            except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                logger.error("Invalid GOOGLE_SERVICE_ACCOUNT_FILE: %s", exc)
        return None

    def _ensure_credentials(self):
        if self._credentials is not None:
            return self._credentials
        if self._credentials_error:
            return None
        info = self._service_account_info
        if not info:
            self._credentials_error = "service account not configured"
            return None
        try:
            from google.oauth2 import service_account
        except ImportError as exc:  # pragma: no cover - optional dependency
            logger.error("google-auth not installed")
            self._credentials_error = "google-auth missing"
            raise RuntimeError("google-auth not installed") from exc

        credentials = service_account.Credentials.from_service_account_info(info, scopes=self._scopes)
        self._credentials = credentials
        return credentials

    @staticmethod
    def _request():
        try:
            from google.auth.transport.requests import Request
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("google-auth not installed") from exc
        return Request()
