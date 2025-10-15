"""Slack notification plugin leveraging lifecycle hooks."""

from __future__ import annotations

import asyncio
from typing import Any

from ..clients.slack_client import slack_client
from ..settings import settings
from .base import PluginBase


class SlackAlertsPlugin(PluginBase):
    name = "slack_followup_alerts"
    description = "Post Slack alerts when follow-ups or feedback are captured"
    hooks = ("post_alignment_followup", "on_feedback")
    required_secrets = ("SLACK_BOT_TOKEN",)

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.channel: str | None = self.config.get("channel") or slack_client.channel

    @property
    def enabled(self) -> bool:
        return self.is_enabled and bool(self.channel)

    def _schedule(self, coro) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coro)
        else:
            loop.create_task(coro)

    def post_alignment_followup(self, event: dict[str, Any], status: str) -> None:
        if not self.enabled or not slack_client.enabled:
            return

        title = event.get("title", "Unknown initiative")
        event_id = event.get("event_id", "n/a")
        link = event.get("notification", {}).get("response", {}).get("permalink")

        message = (
            f"*Follow-up updated*: `{status}`\n"
            f"> *Title*: {title}\n> *Event ID*: {event_id}"
        )
        if link:
            message += f"\n> <{link}|Slack permalink>"

        async def _post() -> None:
            await slack_client.post_digest(message, channel=self.channel)

        self._schedule(_post())

    def on_feedback(self, feedback: dict[str, Any]) -> None:
        if not self.enabled or not slack_client.enabled:
            return

        title = feedback.get("title", "Unknown plan")
        rating = feedback.get("rating")
        comment = feedback.get("comment") or ""
        submitted_by = feedback.get("submitted_by") or "anonymous"
        line = f"*Feedback received* for `{title}` from `{submitted_by}`"
        if rating:
            line += f" (rating {rating}/5)"
        if comment:
            line += f"\n> {comment}"

        async def _post() -> None:
            await slack_client.post_digest(line, channel=self.channel)

        self._schedule(_post())
