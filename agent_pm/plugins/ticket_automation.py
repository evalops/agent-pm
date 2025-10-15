"""Ticket automation plugin that creates Jira issues from plan output."""

from __future__ import annotations

import asyncio
from typing import Any

from ..clients.jira_client import jira_client
from ..settings import settings
from .base import PluginBase


class TicketAutomationPlugin(PluginBase):
    name = "ticket_automation"
    description = "Create Jira issues from generated plans"
    hooks = ("post_plan",)

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.project_key: str | None = self.config.get("project_key") or (settings.allowed_projects[0] if settings.allowed_projects else None)
        self.issue_type: str = self.config.get("issue_type", "Task")
        self.summary_prefix: str = self.config.get("summary_prefix", "[Plan]")
        self.watchers: list[str] = self.config.get("watchers", [])

    @property
    def enabled(self) -> bool:
        return bool(self.project_key)

    def post_plan(self, plan: dict[str, Any], context: dict[str, Any]) -> None:
        if not self.enabled:
            return

        async def _run() -> None:
            payload = self._build_payload(plan, context)
            result = await jira_client.create_issue(payload)
            plan.setdefault("plugins", {})[self.name] = {"payload": payload, "result": result}

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_run())
        else:
            loop.create_task(_run())

    def _build_payload(self, plan: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        title = context.get("title") or plan.get("title", "Generated Plan")
        summary = f"{self.summary_prefix} {title}".strip()
        description_lines = [
            f"h1. {title}",
            "",
            "*Generated PRD:*",
            plan.get("prd_markdown", "(not available)"),
        ]
        requirements = context.get("requirements") or plan.get("requirements")
        if requirements:
            desc = "\n".join(f"* {req}" for req in requirements)
            description_lines.extend(["", "*Top requirements:*", desc])

        payload: dict[str, Any] = {
            "fields": {
                "project": {"key": self.project_key},
                "summary": summary,
                "issuetype": {"name": self.issue_type},
                "description": "\n\n".join(description_lines),
            }
        }
        if self.watchers:
            payload["update"] = {
                "watcher": [{"add": {"accountId": watcher}} for watcher in self.watchers]
            }
        return payload
