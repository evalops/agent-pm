"""GraphQL GitHub client stub for project notes."""

from typing import Any

import httpx

from ..settings import settings


class GitHubClient:
    def __init__(self) -> None:
        self.token = settings.github_token

    @property
    def enabled(self) -> bool:
        return self.token is not None

    async def add_project_note(self, project_id: str, note: str) -> dict[str, Any]:
        if settings.dry_run or not self.enabled:
            return {"dry_run": True, "project_id": project_id, "note": note}

        query = """
        mutation($projectId: ID!, $body: String!) {
          updateProjectV2DraftIssue(input: {projectId: $projectId, body: $body}) {
            projectItem {
              id
            }
          }
        }
        """
        variables = {"projectId": project_id, "body": note}

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.github.com/graphql",
                json={"query": query, "variables": variables},
                headers=headers,
                timeout=30,
            )
        response.raise_for_status()
        return response.json()


github_client = GitHubClient()


__all__ = ["github_client", "GitHubClient"]
