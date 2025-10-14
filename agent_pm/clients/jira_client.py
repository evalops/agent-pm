"""HTTPX-based Jira client with dry-run support."""

from typing import Any

import httpx

from ..settings import settings


class JiraClient:
    def __init__(self) -> None:
        self.base_url = settings.jira_base_url
        self.email = settings.jira_email
        self.api_token = settings.jira_api_token

    @property
    def enabled(self) -> bool:
        return all([self.base_url, self.email, self.api_token])

    async def create_issue(self, payload: dict[str, Any]) -> dict[str, Any]:
        if settings.dry_run or not self.enabled:
            return {"dry_run": True, "payload": payload}

        auth = (self.email, self.api_token)  # type: ignore[arg-type]
        url = f"{self.base_url}/rest/api/3/issue"
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                auth=auth,
                json=payload,
                headers={"Accept": "application/json"},
                timeout=30,
            )
        response.raise_for_status()
        return response.json()


jira_client = JiraClient()


__all__ = ["jira_client", "JiraClient"]
