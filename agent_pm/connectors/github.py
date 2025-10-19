"""GitHub connector for synchronizing repositories, issues, and pull requests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from agent_pm.connectors.base import Connector
from agent_pm.settings import settings


class GitHubConnector(Connector):
    def __init__(self) -> None:
        super().__init__(name="github")
        self._token = settings.github_token
        self._repositories = settings.github_repositories
        self._base_url = "https://api.github.com"

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._repositories)

    async def _fetch(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if settings.dry_run or not self.enabled:
            return {"dry_run": True, "path": path, "params": params or {}, "repositories": self._repositories}

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
        }
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self._base_url}{path}", headers=headers, params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    async def _sync_repo(self, repo: str, since: datetime | None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if since:
            params["since"] = since.astimezone(UTC).isoformat()

        repo_data = await self._fetch(f"/repos/{repo}")
        issues = await self._fetch(f"/repos/{repo}/issues", params)
        pulls = await self._fetch(f"/repos/{repo}/pulls", params)
        return {
            "repository": repo,
            "repository_data": repo_data,
            "issues": issues,
            "pull_requests": pulls,
        }

    async def sync(self, *, since: datetime | None = None) -> list[dict[str, Any]]:
        if not self._repositories:
            return []
        payloads = []
        for repo in self._repositories:
            payloads.append(await self._sync_repo(repo, since))
        return payloads
