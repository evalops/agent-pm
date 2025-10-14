"""Client access points."""

from .calendar_client import CalendarClient, calendar_client
from .github_client import GitHubClient, github_client
from .jira_client import JiraClient, jira_client
from .openai_client import OpenAIClient, openai_client
from .slack_client import SlackClient, slack_client

__all__ = [
    "CalendarClient",
    "calendar_client",
    "GitHubClient",
    "github_client",
    "JiraClient",
    "jira_client",
    "OpenAIClient",
    "openai_client",
    "SlackClient",
    "slack_client",
]
