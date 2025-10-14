"""Pydantic models used by FastAPI routes."""

from pydantic import BaseModel, Field


class Idea(BaseModel):
    title: str = Field(..., description="Idea title")
    context: str | None = Field("", description="Background information")
    constraints: list[str] | None = Field(default_factory=list, description="Constraints")
    enable_tools: bool | None = Field(
        default=None,
        description="Allow planner agent to call external tools (overrides default when set)",
    )


class TicketPlan(BaseModel):
    project_key: str
    stories: list[str]


class JiraIssuePayload(BaseModel):
    project_key: str
    summary: str
    description: str
    issue_type: str = "Story"
    labels: list[str] = Field(default_factory=list)

    def to_jira(self) -> dict:
        return {
            "fields": {
                "project": {"key": self.project_key},
                "summary": self.summary,
                "description": self.description,
                "issuetype": {"name": self.issue_type},
                "labels": self.labels,
            }
        }


class SlackDigest(BaseModel):
    body_md: str
    channel: str | None = None


class ReviewEvent(BaseModel):
    summary: str
    description: str
    start_time_iso: str
    duration_minutes: int = 30
    attendees: list[str] = Field(default_factory=list)


class BatchIdea(BaseModel):
    """Batch planning request."""

    ideas: list[Idea]


__all__ = ["Idea", "TicketPlan", "JiraIssuePayload", "SlackDigest", "ReviewEvent", "BatchIdea"]
