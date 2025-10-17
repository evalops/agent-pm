"""Slack client for posting status digests."""

from typing import Any

import httpx

from ..settings import settings
from ..utils import with_exponential_backoff


class SlackClient:
    def __init__(self) -> None:
        self.token = settings.slack_bot_token
        self.channel = settings.slack_status_channel

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.channel)

    async def post_digest(
        self, body_md: str, channel: str | None = None
    ) -> dict[str, Any]:
        if not body_md:
            raise ValueError("Slack digest body must not be empty")
        channel = channel or self.channel
        if not channel:
            raise ValueError("Slack channel is required")
        payload = {"channel": channel, "text": body_md, "mrkdwn": True}
        if settings.dry_run or not self.enabled:
            return {"dry_run": True, "payload": payload}

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json; charset=utf-8",
        }

        async def _send() -> dict[str, Any]:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers=headers,
                    json=payload,
                    timeout=30,
                )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok", True):
                raise RuntimeError(f"Slack API error: {data}")
            return data

        return await with_exponential_backoff(_send)


slack_client = SlackClient()


__all__ = ["slack_client", "SlackClient"]
