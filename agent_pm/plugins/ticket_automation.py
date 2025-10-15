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
    hooks = ("pre_plan", "post_plan", "post_alignment_event", "post_ticket_export")

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.project_key: str | None = self.config.get("project_key") or (settings.allowed_projects[0] if settings.allowed_projects else None)
        self.issue_type: str = self.config.get("issue_type", "Task")
        self.summary_prefix: str = self.config.get("summary_prefix", "[Plan]")
        self.watchers: list[str] = self.config.get("watchers", [])
        self.plan_contexts: list[dict[str, Any]] = []
        self.alignment_events: list[str] = []
        self.export_events: list[dict[str, Any]] = []

    @property
    def enabled(self) -> bool:
        return bool(self.project_key)

    def pre_plan(self, context: dict[str, Any], **kwargs: Any) -> None:
        self.plan_contexts.append(dict(context))
        if len(self.plan_contexts) > 10:
            self.plan_contexts = self.plan_contexts[-10:]

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

    def post_alignment_event(self, event: dict[str, Any], **kwargs: Any) -> None:
        event_id = event.get("event_id") if isinstance(event, dict) else None
        if event_id:
            self.alignment_events.append(event_id)
            if len(self.alignment_events) > 50:
                self.alignment_events = self.alignment_events[-50:]

    def post_ticket_export(
        self,
        kind: str,
        destination: str,
        rows: int,
        statuses: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        entry = {"kind": kind, "destination": destination, "rows": rows}
        if statuses is not None:
            entry["statuses"] = statuses
        self.export_events.append(entry)
        if len(self.export_events) > 25:
            self.export_events = self.export_events[-25:]

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
