from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from agent_pm.procedures import loader
from agent_pm.settings import settings


class _FakeResponse:
    def __init__(self, *, json_data: Any = None, text: str = "") -> None:
        self._json_data = json_data
        self.text = text

    def json(self) -> Any:
        return self._json_data

    def raise_for_status(self) -> None:
        return None


def _fake_github_client_factory(
    *,
    pulls: list[dict[str, Any]],
    calls: list[dict[str, Any]],
    diffs: dict[str, str] | None = None,
):
    diff_payloads = diffs or {}

    class _FakeGitHubClient:
        async def __aenter__(self) -> _FakeGitHubClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(
            self,
            url: str,
            *,
            headers: dict[str, str] | None = None,
            params: dict[str, Any] | None = None,
            timeout: int | None = None,
        ) -> _FakeResponse:
            calls.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
            if url.endswith("/pulls"):
                return _FakeResponse(json_data=pulls)
            if url in diff_payloads:
                return _FakeResponse(text=diff_payloads[url])
            raise AssertionError(f"Unexpected URL: {url}")

    return _FakeGitHubClient


@pytest.mark.asyncio
async def test_github_pr_scan_does_not_narrow_dependabot_mentions(monkeypatch):
    import agent_pm.procedure_runner as procedure_runner

    calls: list[dict[str, Any]] = []
    pulls = [
        {
            "number": 1,
            "title": "Regular cleanup",
            "user": {"login": "alice"},
            "created_at": "2026-06-14T00:00:00Z",
            "diff_url": "https://example.test/pr-1.diff",
        },
        {
            "number": 2,
            "title": "Bump requests",
            "user": {"login": "dependabot[bot]"},
            "created_at": "2026-06-14T01:00:00Z",
            "diff_url": "https://example.test/pr-2.diff",
        },
    ]

    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(settings, "github_token", "token")
    monkeypatch.setattr(settings, "github_repositories", ["evalops/platform"])
    monkeypatch.setattr(
        procedure_runner.httpx,
        "AsyncClient",
        _fake_github_client_factory(pulls=pulls, calls=calls),
    )

    result = await procedure_runner._run_github_pr_scan(
        "Scan open PRs. Flag security bumps (dependabot with GHSA refs)."
    )

    assert result["author"] is None
    assert result["include_agent_authors"] is False
    assert {pr["number"] for pr in result["prs"]} == {1, 2}
    assert calls[0]["url"].endswith("/pulls")


@pytest.mark.asyncio
async def test_github_pr_scan_merges_evalops_defaults_with_explicit_repo(monkeypatch):
    import agent_pm.procedure_runner as procedure_runner

    monkeypatch.setattr(settings, "dry_run", True)
    monkeypatch.setattr(settings, "github_token", None)
    monkeypatch.setattr(settings, "github_repositories", ["evalops/platform", "evalops/deploy"])

    result = await procedure_runner._run_github_pr_scan("Scan open PRs across evalops repos and haasonsaas/homelab.")

    assert result["repositories"] == ["evalops/platform", "evalops/deploy", "haasonsaas/homelab"]


@pytest.mark.asyncio
async def test_github_pr_scan_paginates_all_open_pr_pages(monkeypatch):
    import agent_pm.procedure_runner as procedure_runner

    pages = {
        1: [
            {"number": index, "user": {"login": "alice"}, "created_at": "2026-06-14T00:00:00Z"}
            for index in range(1, 21)
        ],
        2: [{"number": 21, "user": {"login": "alice"}, "created_at": "2026-06-14T00:00:00Z"}],
    }
    calls: list[dict[str, Any]] = []

    class _FakeGitHubClient:
        async def __aenter__(self) -> _FakeGitHubClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(
            self,
            url: str,
            *,
            headers: dict[str, str] | None = None,
            params: dict[str, Any] | None = None,
            timeout: int | None = None,
        ) -> _FakeResponse:
            calls.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
            page = int((params or {}).get("page", 1))
            return _FakeResponse(json_data=pages[page])

    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(settings, "github_token", "token")
    monkeypatch.setattr(settings, "github_repositories", ["evalops/platform"])
    monkeypatch.setattr(procedure_runner.httpx, "AsyncClient", lambda: _FakeGitHubClient())

    result = await procedure_runner._run_github_pr_scan("Scan open PRs across evalops repos.")

    assert result["count"] == 21
    assert [call["params"]["page"] for call in calls] == [1, 2]


@pytest.mark.asyncio
async def test_github_pr_scan_fetches_diffs_for_agent_authors(monkeypatch):
    import agent_pm.procedure_runner as procedure_runner

    now = datetime.now(tz=UTC)
    recent = now.isoformat().replace("+00:00", "Z")
    calls: list[dict[str, Any]] = []
    pulls = [
        {
            "number": 1,
            "title": "Security bump",
            "user": {"login": "dependabot[bot]"},
            "created_at": recent,
            "diff_url": "https://example.test/pr-1.diff",
        },
        {
            "number": 2,
            "title": "Generated refactor",
            "user": {"login": "codex"},
            "created_at": recent,
            "diff_url": "https://example.test/pr-2.diff",
        },
        {
            "number": 3,
            "title": "Manual change",
            "user": {"login": "alice"},
            "created_at": recent,
            "diff_url": "https://example.test/pr-3.diff",
        },
    ]
    diffs = {
        "https://example.test/pr-1.diff": "diff --git a/a.py b/a.py\n+secret = 'nope'\n",
        "https://example.test/pr-2.diff": "diff --git a/b.py b/b.py\n+print('hello')\n",
    }

    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(settings, "github_token", "token")
    monkeypatch.setattr(settings, "github_repositories", ["evalops/platform"])
    monkeypatch.setattr(
        procedure_runner.httpx,
        "AsyncClient",
        _fake_github_client_factory(pulls=pulls, diffs=diffs, calls=calls),
    )

    result = await procedure_runner._run_github_pr_scan(
        "List PRs opened in the last 24 hours. "
        "Filter to PRs authored by bots or agents (dependabot, codex, cursor, maestro, claude). "
        "For each PR, fetch the diff."
    )

    assert result["author"] is None
    assert result["fetch_diffs"] is True
    assert {pr["user"]["login"] for pr in result["prs"]} == {"dependabot[bot]", "codex"}
    assert all(pr["diff"].startswith("diff --git") for pr in result["prs"])
    assert sum(1 for call in calls if call["headers"]["Accept"] == "application/vnd.github.v3.diff") == 2


@pytest.mark.asyncio
async def test_github_pr_scan_uses_api_url_for_github_diff_endpoints(monkeypatch):
    import agent_pm.procedure_runner as procedure_runner

    now = datetime.now(tz=UTC)
    recent = now.isoformat().replace("+00:00", "Z")
    calls: list[dict[str, Any]] = []
    pulls = [
        {
            "number": 1,
            "title": "Security bump",
            "user": {"login": "dependabot[bot]"},
            "created_at": recent,
            "url": "https://api.github.com/repos/evalops/platform/pulls/1",
            "diff_url": "https://github.com/evalops/platform/pull/1.diff",
        }
    ]
    diffs = {
        "https://api.github.com/repos/evalops/platform/pulls/1": "diff --git a/a.py b/a.py\n+secret = 'nope'\n",
    }

    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(settings, "github_token", "token")
    monkeypatch.setattr(settings, "github_repositories", ["evalops/platform"])
    monkeypatch.setattr(
        procedure_runner.httpx,
        "AsyncClient",
        _fake_github_client_factory(pulls=pulls, diffs=diffs, calls=calls),
    )

    result = await procedure_runner._run_github_pr_scan(
        "List PRs opened in the last 24 hours. "
        "Filter to PRs authored by bots or agents (dependabot, codex, cursor, maestro, claude). "
        "For each PR, fetch the diff."
    )

    assert result["count"] == 1
    diff_call = next(call for call in calls if call["headers"]["Accept"] == "application/vnd.github.v3.diff")
    assert diff_call["url"] == "https://api.github.com/repos/evalops/platform/pulls/1"


@pytest.mark.asyncio
async def test_run_sentry_scan_extracts_query_from_instruction(monkeypatch):
    import agent_pm.procedure_runner as procedure_runner

    captured: dict[str, Any] = {}

    async def fake_list_issues(*, query: str, stats_period: str = "14d", limit: int = 10):
        captured["query"] = query
        captured["stats_period"] = stats_period
        captured["limit"] = limit
        return [{"id": "issue-1"}]

    async def fake_error_counts(*, stats_period: str = "7d", project: str | None = None):
        captured["error_counts_period"] = stats_period
        captured["project"] = project
        return {"data": []}

    monkeypatch.setattr(procedure_runner.sentry_connector, "list_issues", fake_list_issues)
    monkeypatch.setattr(procedure_runner.sentry_connector, "error_counts", fake_error_counts)

    result = await procedure_runner._run_sentry_scan("List resolved Sentry issues (is:resolved, environment:prod, 7d).")

    assert captured["query"] == "is:resolved environment:prod"
    assert captured["stats_period"] == "7d"
    assert captured["limit"] == 10
    assert captured["error_counts_period"] == "7d"
    assert result["query"] == "is:resolved environment:prod"


@pytest.mark.asyncio
async def test_linear_scan_respects_assignment_and_stale_rules(monkeypatch):
    import agent_pm.procedure_runner as procedure_runner

    now = datetime.now(tz=UTC)
    issues = [
        {
            "id": "issue-1",
            "identifier": "LIN-1",
            "title": "Past due issue",
            "state": {"name": "Todo"},
            "assignee": {"email": "me@example.com"},
            "dueDate": (now - timedelta(days=1)).date().isoformat(),
            "updatedAt": (now - timedelta(days=3)).isoformat().replace("+00:00", "Z"),
        },
        {
            "id": "issue-2",
            "identifier": "LIN-2",
            "title": "Needs comment",
            "state": {"name": "In Progress"},
            "assignee": {"email": "me@example.com"},
            "dueDate": None,
            "updatedAt": (now - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        },
        {
            "id": "issue-3",
            "identifier": "LIN-3",
            "title": "Someone else's issue",
            "state": {"name": "Todo"},
            "assignee": {"email": "other@example.com"},
            "dueDate": (now - timedelta(days=1)).date().isoformat(),
            "updatedAt": (now - timedelta(days=4)).isoformat().replace("+00:00", "Z"),
        },
    ]
    captured: dict[str, Any] = {}

    async def fake_list_issues(*, assignee_email=None, team_id=None, state=None, order_by="updatedAt", limit=50):
        captured["assignee_email"] = assignee_email
        captured["team_id"] = team_id
        captured["state"] = state
        captured["order_by"] = order_by
        captured["limit"] = limit
        return issues

    async def fake_get_issue_comments(issue_id: str, limit: int = 20):
        captured.setdefault("comment_ids", []).append(issue_id)
        if issue_id == "issue-2":
            return []
        return [{"createdAt": now.isoformat().replace("+00:00", "Z")}]

    monkeypatch.setattr(settings, "jira_email", "me@example.com")
    monkeypatch.setattr(procedure_runner.linear_connector, "list_issues", fake_list_issues)
    monkeypatch.setattr(procedure_runner.linear_connector, "get_issue_comments", fake_get_issue_comments)

    result = await procedure_runner._run_linear_scan(
        "Pull all issues assigned to me, sorted by updatedAt ascending. "
        'Flag items: due date blown, last updated > 2 days ago, "In Progress" without recent comments. '
        "Return a list of stale items with IDs and recommended actions."
    )

    stale_by_id = {issue["identifier"]: issue for issue in result["stale"]}
    assert captured["assignee_email"] == "me@example.com"
    assert captured["state"] is None
    assert captured["limit"] is None
    assert result["count"] == 2
    assert result["assignee_email"] == "me@example.com"
    assert stale_by_id["LIN-1"]["flags"] == ["past_due", "stale"]
    assert stale_by_id["LIN-2"]["flags"] == ["in_progress_no_recent_comments"]
    assert captured["comment_ids"] == ["issue-2"]


@pytest.mark.asyncio
async def test_linear_scan_uses_configured_team_ids_when_unscoped(monkeypatch):
    import agent_pm.procedure_runner as procedure_runner

    captured: list[str | None] = []

    async def fake_list_issues(*, team_id=None, assignee_email=None, state=None, order_by="updatedAt", limit=50):
        captured.append(team_id)
        return [{"id": f"{team_id}-1", "identifier": f"{team_id}-1", "title": f"Issue for {team_id}"}]

    monkeypatch.setattr(settings, "linear_team_ids", ["team-1", "team-2"])
    monkeypatch.setattr(procedure_runner.linear_connector, "list_issues", fake_list_issues)

    result = await procedure_runner._run_linear_scan("List all issues sorted by updatedAt ascending.")

    assert captured == ["team-1", "team-2"]
    assert result["count"] == 2
    assert {issue["identifier"] for issue in result["issues"]} == {"team-1-1", "team-2-1"}


@pytest.mark.asyncio
async def test_weekly_progress_review_uses_calendar_scan(monkeypatch):
    import agent_pm.procedure_runner as procedure_runner

    now = datetime.now(tz=UTC)
    captured: dict[str, Any] = {}

    class _FakeCalendarConnector:
        async def sync(self, *, since=None):
            captured["since"] = since
            return [
                {
                    "items": [
                        {
                            "id": "evt-0",
                            "summary": "Earlier today",
                            "start": {"dateTime": (now - timedelta(hours=1)).isoformat()},
                        },
                        {
                            "id": "evt-1",
                            "summary": "Weekly review",
                            "start": {"dateTime": (now + timedelta(days=1)).isoformat()},
                        },
                        {
                            "id": "evt-2",
                            "summary": "Later event",
                            "start": {"dateTime": (now + timedelta(days=10)).isoformat()},
                        },
                    ]
                }
            ]

    monkeypatch.setattr(procedure_runner, "CalendarConnector", lambda: _FakeCalendarConnector())

    result = await procedure_runner._run_calendar_scan("List upcoming calendar events for this week.")
    procedure = loader.load()["weekly_progress_review"]

    assert captured["since"] == now.replace(hour=0, minute=0, second=0, microsecond=0)
    assert result["count"] == 2
    assert procedure["steps"][0]["run"] == "calendar_scan"
    assert "calendar_overview" in procedure["steps"][4]["input"]


@pytest.mark.asyncio
async def test_execute_procedure_returns_step_outputs(monkeypatch):
    import agent_pm.procedure_runner as procedure_runner

    procedure = {
        "name": "weekly_progress_review",
        "description": "Multi-source progress sweep.",
        "steps": [
            {"id": "calendar_overview", "run": "calendar_scan"},
            {"id": "compose_digest", "run": "model"},
        ],
    }

    async def fake_execute_step(step: dict[str, Any], context: dict[str, Any]) -> Any:
        if step["id"] == "calendar_overview":
            return {"count": 2, "events": [{"id": "evt-1"}]}
        assert context["calendar_overview"] == {"count": 2, "events": [{"id": "evt-1"}]}
        return "Digest body"

    monkeypatch.setattr(procedure_runner.loader, "load", lambda: {"weekly_progress_review": procedure})
    monkeypatch.setattr(procedure_runner, "_execute_step", fake_execute_step)

    result = await procedure_runner.execute_procedure("weekly_progress_review")

    assert result["procedure"] == "weekly_progress_review"
    assert result["title"] == "weekly_progress_review"
    assert result["description"] == "Multi-source progress sweep."
    assert result["dry_run"] is False
    assert result["calendar_overview"] == {"count": 2, "events": [{"id": "evt-1"}]}
    assert result["compose_digest"] == "Digest body"
    assert result["digest"] == "Digest body"


@pytest.mark.asyncio
async def test_linear_scan_requires_configured_email_for_assigned_to_me(monkeypatch):
    import agent_pm.procedure_runner as procedure_runner

    captured: dict[str, Any] = {}

    async def fake_list_issues(**kwargs):
        captured["called"] = True
        return []

    monkeypatch.setattr(settings, "jira_email", None)
    monkeypatch.setattr(settings, "google_calendar_delegated_user", None)
    monkeypatch.setattr(procedure_runner.linear_connector, "list_issues", fake_list_issues)

    result = await procedure_runner._run_linear_scan(
        "Pull all issues assigned to me, sorted by updatedAt ascending. Return stale items with recommended actions."
    )

    assert "called" not in captured
    assert result["issues"] == []
    assert result["stale"] == []
    assert result["error"] == "Linear 'assigned to me' scans require JIRA_EMAIL or GOOGLE_CALENDAR_DELEGATED_USER."
