"""Notion connector with dry-run payloads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from agent_pm.connectors.base import Connector
from agent_pm.settings import settings


class NotionConnector(Connector):
    def __init__(self) -> None:
        super().__init__(name="notion")
        self._token = settings.notion_api_token
        self._database_ids = settings.notion_database_ids

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._database_ids)

    async def _query_database(self, database_id: str, since: datetime | None) -> dict[str, Any]:
        if settings.dry_run or not self.enabled:
            return {"dry_run": True, "database_id": database_id, "since": since.isoformat() if since else None}

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {}
        if since:
            payload["filter"] = {
                "property": "last_edited_time",
                "date": {"after": since.astimezone(UTC).isoformat()},
            }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.notion.com/v1/databases/{database_id}/query",
                headers=headers,
                json=payload,
                timeout=30,
            )
        response.raise_for_status()
        return response.json()

    async def sync(self, *, since: datetime | None = None) -> list[dict[str, Any]]:
        payloads = []
        for database_id in self._database_ids:
            payloads.append(await self._query_database(database_id, since))
        return payloads
