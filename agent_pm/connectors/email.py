"""Gmail connector returning structured message payloads."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from agent_pm.connectors.base import Connector
from agent_pm.settings import settings

logger = logging.getLogger(__name__)


class EmailConnector(Connector):
    def __init__(self) -> None:
        super().__init__(name="gmail")
        self._delegated_user = settings.gmail_delegated_user
        self._labels = settings.gmail_label_filter
        self._scopes = settings.gmail_scopes

    @property
    def enabled(self) -> bool:
        json_creds = settings.gmail_service_account_json
        file_creds = settings.gmail_service_account_file
        return bool(json_creds or (file_creds and Path(file_creds).exists()))

    def _build_query(self, since: datetime | None) -> str:
        clauses: list[str] = []
        if since:
            clauses.append(f"after:{since.strftime('%Y/%m/%d')}")
        if self._labels:
            clauses.append(" OR ".join(f"label:{label}" for label in self._labels))
        return " ".join(filter(None, clauses))

    async def sync(self, *, since: datetime | None = None) -> list[dict[str, Any]]:
        query = self._build_query(since)
        if settings.dry_run or not self.enabled:
            return [
                {
                    "dry_run": True,
                    "query": query,
                    "labels": self._labels,
                    "delegated_user": self._delegated_user,
                }
            ]

        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        params = {"q": query} if query else {}
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                headers=headers,
                params=params,
                timeout=30,
            )
        response.raise_for_status()
        message_list = response.json()

        ids = [item.get("id") for item in message_list.get("messages", []) if item.get("id")]
        if not ids:
            return [message_list]

        messages: list[dict[str, Any]] = []
        async with httpx.AsyncClient() as client:
            for message_id in ids:
                detail = await client.get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
                    headers=headers,
                    timeout=30,
                )
                detail.raise_for_status()
                data = detail.json()
                payload = data.get("payload", {})
                headers_list = payload.get("headers", [])
                header_map = {item.get("name"): item.get("value") for item in headers_list}
                messages.append(
                    {
                        "id": message_id,
                        "thread_id": data.get("threadId"),
                        "snippet": data.get("snippet"),
                        "subject": header_map.get("Subject"),
                        "from": header_map.get("From"),
                        "to": header_map.get("To"),
                        "date": header_map.get("Date"),
                        "labels": data.get("labelIds", []),
                    }
                )
        return [message_list, {"messages": messages}]

    async def _get_token(self) -> str:
        creds = await asyncio.to_thread(self._load_credentials)
        if not creds:
            raise RuntimeError("Gmail credentials missing")
        if not creds.valid or creds.expired:
            await asyncio.to_thread(creds.refresh, self._request())
        if not creds.token:
            raise RuntimeError("Failed to refresh Gmail access token")
        return creds.token

    def _load_credentials(self):
        info: dict[str, Any] | None = None
        if settings.gmail_service_account_json:
            try:
                info = json.loads(settings.gmail_service_account_json)
            except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                logger.error("Invalid GMAIL_SERVICE_ACCOUNT_JSON: %s", exc)
        elif settings.gmail_service_account_file:
            try:
                info = json.loads(Path(settings.gmail_service_account_file).read_text(encoding="utf-8"))
            except FileNotFoundError:
                logger.error("GMAIL_SERVICE_ACCOUNT_FILE not found: %s", settings.gmail_service_account_file)
            except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                logger.error("Invalid GMAIL_SERVICE_ACCOUNT_FILE: %s", exc)
        if not info:
            return None

        try:
            from google.oauth2 import service_account
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("google-auth not installed") from exc

        credentials = service_account.Credentials.from_service_account_info(info, scopes=self._scopes)
        if self._delegated_user:
            credentials = credentials.with_subject(self._delegated_user)
        return credentials

    @staticmethod
    def _request():
        try:
            from google.auth.transport.requests import Request
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("google-auth not installed") from exc
        return Request()
