"""Google Calendar client for scheduling stakeholder reviews."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from ..settings import settings
from ..utils import with_exponential_backoff

logger = logging.getLogger(__name__)

GOOGLE_CALENDAR_BASE_URL = "https://www.googleapis.com/calendar/v3"


class CalendarClient:
    def __init__(self) -> None:
        self.calendar_id = settings.calendar_id
        self._scopes = settings.google_calendar_scopes
        self._delegated_user = settings.google_calendar_delegated_user
        self._credentials = self._load_credentials()
        self._token_lock = asyncio.Lock()

    def _load_credentials(self):
        info: dict[str, Any] | None = None

        if settings.google_service_account_json:
            try:
                info = json.loads(settings.google_service_account_json)
            except json.JSONDecodeError as exc:  # pragma: no cover - configuration error
                logger.error("Invalid GOOGLE_SERVICE_ACCOUNT_JSON: %s", exc)
        elif settings.google_service_account_file:
            path = Path(settings.google_service_account_file)
            if path.is_file():
                try:
                    info = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:  # pragma: no cover - configuration error
                    logger.error("Invalid service account file JSON: %s", exc)
            else:  # pragma: no cover - configuration error
                logger.error("GOOGLE_SERVICE_ACCOUNT_FILE not found: %s", path)

        if not info:
            return None

        credentials = service_account.Credentials.from_service_account_info(info, scopes=self._scopes)
        if self._delegated_user:
            credentials = credentials.with_subject(self._delegated_user)
        return credentials

    @property
    def enabled(self) -> bool:
        return bool(self.calendar_id and self._credentials)

    async def _get_access_token(self) -> str:
        if not self._credentials:
            raise RuntimeError("Google Calendar credentials are not configured")

        async with self._token_lock:

            def _refresh() -> str:
                if not self._credentials.valid or self._credentials.expired:
                    self._credentials.refresh(Request())
                token = self._credentials.token
                if not token:  # pragma: no cover - defensive
                    raise RuntimeError("Failed to refresh Google Calendar access token")
                return token

            return await asyncio.to_thread(_refresh)

    @staticmethod
    def _ensure_datetime(value: datetime) -> tuple[str, str]:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            value = value.replace(tzinfo=UTC)
        tz = value.tzinfo.tzname(value) or "UTC"
        return value.isoformat(), tz

    async def schedule_review(
        self,
        summary: str,
        description: str,
        start_time: datetime,
        duration_minutes: int = 30,
        attendees: list[str] | None = None,
    ) -> dict[str, Any]:
        if not summary:
            raise ValueError("Calendar event summary required")
        if duration_minutes <= 0:
            raise ValueError("duration_minutes must be positive")

        attendees = attendees or []
        start_iso, start_tz = self._ensure_datetime(start_time)
        end_iso, end_tz = self._ensure_datetime(start_time + timedelta(minutes=duration_minutes))

        event: dict[str, Any] = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_iso, "timeZone": start_tz},
            "end": {"dateTime": end_iso, "timeZone": end_tz},
        }

        if attendees:
            event["attendees"] = [{"email": email} for email in attendees]

        if settings.dry_run or not self.enabled:
            return {"dry_run": True, "event": event}

        encoded_calendar_id = quote_plus(self.calendar_id)
        url = f"{GOOGLE_CALENDAR_BASE_URL}/calendars/{encoded_calendar_id}/events"

        async def _send() -> dict[str, Any]:
            token = await self._get_access_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            params = {
                "sendUpdates": "all" if attendees else "none",
            }
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, params=params, json=event, timeout=30)
            response.raise_for_status()
            return response.json()

        return await with_exponential_backoff(_send)


calendar_client = CalendarClient()


__all__ = ["calendar_client", "CalendarClient"]
