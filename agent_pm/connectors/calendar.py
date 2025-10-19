"""Google Calendar connector with dry-run support."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from agent_pm.connectors.base import Connector
from agent_pm.settings import settings

logger = logging.getLogger(__name__)


class CalendarConnector(Connector):
    def __init__(self) -> None:
        super().__init__(name="calendar")
        self._scopes = settings.google_calendar_scopes
        self._delegated_user = settings.google_calendar_delegated_user
        self._service_account_info = self._load_service_account_info()
        self._credentials = None
        self._credentials_error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(settings.calendar_id and self._service_account_info)

    async def sync(self, *, since: datetime | None = None) -> list[dict[str, Any]]:
        calendar_id = settings.calendar_id
        window_days = settings.calendar_sync_window_days
        if settings.dry_run or not self.enabled:
            return [
                {
                    "dry_run": True,
                    "calendar_id": calendar_id,
                    "since": since.isoformat() if since else None,
                    "window_days": window_days,
                }
            ]

        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        lower_bound = datetime.now(tz=UTC) - timedelta(days=window_days)
        time_min = (since if since and since > lower_bound else lower_bound).isoformat()
        time_max = (datetime.now(tz=UTC) + timedelta(days=window_days)).isoformat()
        params = {"timeMin": time_min, "timeMax": time_max, "singleEvents": "true", "orderBy": "startTime"}
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
                headers=headers,
                params=params,
                timeout=30,
            )
        response.raise_for_status()
        return [response.json()]

    async def _get_token(self) -> str:
        creds = await asyncio.to_thread(self._ensure_credentials)
        if not creds:
            raise RuntimeError("Google Calendar credentials missing")
        if not creds.valid or creds.expired:
            await asyncio.to_thread(creds.refresh, self._request())
        if not creds.token:
            raise RuntimeError("Failed to obtain calendar token")
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
        if self._delegated_user:
            credentials = credentials.with_subject(self._delegated_user)
        self._credentials = credentials
        return credentials

    @staticmethod
    def _request():
        try:
            from google.auth.transport.requests import Request
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("google-auth not installed") from exc
        return Request()
