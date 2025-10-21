"""PagerDuty client for incident notifications."""

from __future__ import annotations

from typing import Any

import httpx

from agent_pm.settings import settings


class PagerDutyClient:
    def __init__(self) -> None:
        self.routing_key = settings.pagerduty_routing_key

    @property
    def enabled(self) -> bool:
        return bool(self.routing_key)

    async def trigger_incident(
        self, summary: str, source: str = "agent-pm", severity: str = "error", **details: Any
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"dry_run": True, "summary": summary, "details": details}

        payload = {
            "routing_key": self.routing_key,
            "event_action": "trigger",
            "payload": {
                "summary": summary,
                "source": source,
                "severity": severity,
                "custom_details": details,
            },
        }

        async with httpx.AsyncClient() as client:
            response = await client.post("https://events.pagerduty.com/v2/enqueue", json=payload, timeout=10)
            response.raise_for_status()
            return response.json()


pagerduty_client = PagerDutyClient()


__all__ = ["pagerduty_client", "PagerDutyClient"]
