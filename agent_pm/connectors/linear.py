"""Linear connector — GraphQL API for issue tracking and project management."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from agent_pm.connectors.base import Connector
from agent_pm.settings import settings

LINEAR_API_URL = "https://api.linear.app/graphql"


class LinearConnector(Connector):
    def __init__(self) -> None:
        super().__init__(name="linear")
        self._api_key = settings.linear_api_key
        self._team_ids = settings.linear_team_ids

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._api_key or "",
            "Content-Type": "application/json",
        }

    async def _graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        if settings.dry_run or not self.enabled:
            return {"dry_run": True, "query": query[:200], "variables": variables}

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                LINEAR_API_URL,
                headers=self._headers(),
                json={"query": query, "variables": variables or {}},
                timeout=30,
            )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"Linear GraphQL errors: {data['errors']}")
        return data.get("data", data)

    # ── read operations ──────────────────────────────────────────

    async def list_issues(
        self,
        *,
        assignee_id: str | None = None,
        team_id: str | None = None,
        state: str | None = None,
        order_by: str = "updatedAt",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        filters: dict[str, Any] = {}
        if assignee_id:
            filters["assignee"] = {"id": {"eq": assignee_id}}
        if team_id:
            filters["team"] = {"id": {"eq": team_id}}
        state_filter: dict[str, Any] | None = None
        if state:
            state_filter = {"name": {"eq": state}}

        query = """
        query($filter: IssueFilter, $stateFilter: WorkflowStateFilter, $orderBy: PaginationOrderBy, $first: Int!) {
          issues(filter: $filter, state: $stateFilter, orderBy: $orderBy, first: $first) {
            nodes {
              id identifier title description state { name } priority
              assignee { id name email } team { id name key }
              dueDate createdAt updatedAt
              labels { nodes { name } }
              parent { id identifier }
            }
          }
        }
        """
        result = await self._graphql(
            query,
            {
                "filter": filters if filters else None,
                "stateFilter": state_filter,
                "orderBy": order_by,
                "first": limit,
            },
        )
        return result.get("issues", {}).get("nodes", [])

    async def list_teams(self) -> list[dict[str, Any]]:
        query = """
        query {
          teams { nodes { id name key } }
        }
        """
        result = await self._graphql(query)
        return result.get("teams", {}).get("nodes", [])

    async def get_issue_comments(self, issue_id: str, limit: int = 20) -> list[dict[str, Any]]:
        query = """
        query($issueId: String!, $first: Int!) {
          issue(id: $issueId) {
            comments(first: $first) { nodes { id body createdAt user { name } } }
          }
        }
        """
        result = await self._graphql(query, {"issueId": issue_id, "first": limit})
        return result.get("issue", {}).get("comments", {}).get("nodes", [])

    # ── write operations ─────────────────────────────────────────

    async def create_issue(
        self,
        *,
        team_id: str,
        title: str,
        description: str = "",
        assignee_id: str | None = None,
        priority: int | None = None,
        due_date: str | None = None,
    ) -> dict[str, Any]:
        create_input: dict[str, Any] = {
            "teamId": team_id,
            "title": title,
            "description": description,
        }
        if assignee_id:
            create_input["assigneeId"] = assignee_id
        if priority is not None:
            create_input["priority"] = priority
        if due_date:
            create_input["dueDate"] = due_date

        query = """
        mutation($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue { id identifier title url }
          }
        }
        """
        result = await self._graphql(query, {"input": create_input})
        return result.get("issueCreate", {})

    async def update_issue(self, issue_id: str, **fields: Any) -> dict[str, Any]:
        query = """
        mutation($id: String!, $input: IssueUpdateInput!) {
          issueUpdate(id: $id, input: $input) {
            success
            issue { id identifier title }
          }
        }
        """
        result = await self._graphql(query, {"id": issue_id, "input": fields})
        return result.get("issueUpdate", {})

    async def add_comment(self, issue_id: str, body: str) -> dict[str, Any]:
        query = """
        mutation($issueId: String!, $body: String!) {
          commentCreate(input: {issueId: $issueId, body: $body}) {
            success
            comment { id body }
          }
        }
        """
        result = await self._graphql(query, {"issueId": issue_id, "body": body})
        return result.get("commentCreate", {})

    # ── connector protocol ───────────────────────────────────────

    async def sync(self, *, since: datetime | None = None) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        team_ids = self._team_ids
        if not team_ids:
            teams = await self.list_teams()
            team_ids = [t["id"] for t in teams]
        for tid in team_ids:
            issues = await self.list_issues(team_id=tid, limit=50)
            payloads.append({"team_id": tid, "issues": issues})
        return payloads


linear_connector = LinearConnector()

__all__ = ["LinearConnector", "linear_connector"]