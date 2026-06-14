"""Sentry connector — issues, events, error rates for operations awareness."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from agent_pm.connectors.base import Connector
from agent_pm.settings import settings


class SentryConnector(Connector):
    def __init__(self) -> None:
        super().__init__(name="sentry")
        self._auth_token = settings.sentry_auth_token
        self._org_slug = settings.sentry_org_slug
        self._base_url = settings.sentry_base_url or "https://sentry.io/api/0"

    @property
    def enabled(self) -> bool:
        return bool(self._auth_token and self._org_slug)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._auth_token}",
            "Accept": "application/json",
        }

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if settings.dry_run or not self.enabled:
            return {"dry_run": True, "path": path, "params": params}

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base_url}{path}",
                headers=self._headers(),
                params=params,
                timeout=30,
            )
        resp.raise_for_status()
        return resp.json()

    # ── issues ───────────────────────────────────────────────────

    async def list_issues(
        self,
        *,
        query: str = "is:unresolved",
        sort: str = "freq",
        limit: int = 10,
        stats_period: str = "14d",
    ) -> list[dict[str, Any]]:
        """Search unresolved Sentry issues."""
        result = await self._get(
            f"/organizations/{self._org_slug}/issues/",
            {
                "query": query,
                "sort": sort,
                "limit": limit,
                "statsPeriod": stats_period,
            },
        )
        return result if isinstance(result, list) else []

    async def get_issue(self, issue_id: str) -> dict[str, Any]:
        return await self._get(f"/organizations/{self._org_slug}/issues/{issue_id}/")

    async def get_issue_events(self, issue_id: str, limit: int = 10) -> list[dict[str, Any]]:
        return await self._get(
            f"/organizations/{self._org_slug}/issues/{issue_id}/events/",
            {"limit": limit},
        )

    async def get_issue_tag_distribution(self, issue_id: str, tag_key: str) -> list[dict[str, Any]]:
        return await self._get(
            f"/organizations/{self._org_slug}/issues/{issue_id}/tags/{tag_key}/",
        )

    # ── events / error counts ────────────────────────────────────

    async def search_events(
        self,
        *,
        dataset: str = "errors",
        fields: list[str] | None = None,
        query: str = "",
        sort: str = "-timestamp",
        limit: int = 10,
        stats_period: str = "7d",
    ) -> list[dict[str, Any]]:
        """Search Sentry events (errors, spans, logs)."""
        params: dict[str, Any] = {
            "dataset": dataset,
            "sort": sort,
            "per_page": min(limit, 100),
            "statsPeriod": stats_period,
        }
        if fields:
            params["field"] = fields
        if query:
            params["query"] = query
        result = await self._get(
            f"/organizations/{self._org_slug}/events/",
            params,
        )
        return result.get("data", []) if isinstance(result, dict) else []

    async def error_counts(
        self,
        *,
        stats_period: str = "7d",
        project: str | None = None,
    ) -> dict[str, Any]:
        """Get aggregate error counts."""
        fields = ["count()", "project", "issue"]
        params: dict[str, Any] = {
            "dataset": "errors",
            "field": fields,
            "sort": "-count()",
            "statsPeriod": stats_period,
            "per_page": 25,
        }
        if project:
            params["query"] = f"project:{project}"
        result = await self._get(
            f"/organizations/{self._org_slug}/events/",
            params,
        )
        return result if isinstance(result, dict) else {"data": []}

    # ── projects ─────────────────────────────────────────────────

    async def list_projects(self) -> list[dict[str, Any]]:
        result = await self._get(f"/organizations/{self._org_slug}/projects/")
        return result if isinstance(result, list) else []

    # ── connector protocol ───────────────────────────────────────

    async def sync(self, *, since: datetime | None = None) -> list[dict[str, Any]]:
        issues = await self.list_issues(query="is:unresolved", limit=10)
        error_data = await self.error_counts(stats_period="7d")
        return [
            {
                "issues": issues,
                "error_counts": error_data,
                "since": since.isoformat() if since else None,
            }
        ]


sentry_connector = SentryConnector()

__all__ = ["SentryConnector", "sentry_connector"]
