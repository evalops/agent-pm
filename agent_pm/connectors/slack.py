"""Slack connector for periodic message synchronization."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from agent_pm.connectors.base import Connector
from agent_pm.settings import settings


class SlackConnector(Connector):
    def __init__(self) -> None:
        super().__init__(name="slack")
        self._token = settings.slack_bot_token
        self._channels = settings.slack_sync_channels

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._channels)

    async def _fetch_channel_history(self, channel: str, oldest: float | None) -> dict[str, Any]:
        if settings.dry_run or not self.enabled:
            return {"dry_run": True, "channel": channel, "oldest": oldest}

        payload = {"channel": channel}
        if oldest is not None:
            payload["oldest"] = oldest

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json; charset=utf-8",
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://slack.com/api/conversations.history",
                headers=headers,
                params=payload,
                timeout=30,
            )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok", False):
            raise RuntimeError(f"Slack API error: {data}")
        return data

    async def sync(self, *, since: datetime | None = None) -> list[dict[str, Any]]:
        oldest = None
        if since:
            oldest = since.astimezone(UTC).timestamp()
        payloads = []
        for channel in self._channels:
            payloads.append(await self._fetch_channel_history(channel, oldest))
        return payloads
