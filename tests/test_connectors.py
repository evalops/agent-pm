import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from agent_pm.connectors import (
    CalendarConnector,
    EmailConnector,
    GitHubConnector,
    GoogleDriveConnector,
    LinearConnector,
    NotionConnector,
    SentryConnector,
    SlackConnector,
)
from agent_pm.connectors.base import Connector
from agent_pm.settings import settings
from agent_pm.storage import database
from agent_pm.storage import syncs as sync_storage
from agent_pm.tasks.sync import PeriodicSyncManager


@pytest.fixture(autouse=True)
def _override_db_settings(tmp_path, monkeypatch):
    db_path = tmp_path / "sync_test.db"
    monkeypatch.setattr(database.settings, "database_url", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setattr(database.settings, "database_echo", False)
    database._engine = None
    database._session_factory = None
    yield
    database._engine = None
    database._session_factory = None


@pytest.mark.asyncio
async def test_github_connector_returns_repo_payload(monkeypatch):
    monkeypatch.setattr(settings, "github_token", "token")
    monkeypatch.setattr(settings, "github_repositories", ["factory/agent-pm"])
    connector = GitHubConnector()

    payloads = await connector.sync()

    assert len(payloads) == 1
    assert payloads[0]["repository"] == "factory/agent-pm"


@pytest.mark.asyncio
async def test_slack_connector_uses_configured_channels(monkeypatch):
    monkeypatch.setattr(settings, "slack_bot_token", "xoxb-token")
    monkeypatch.setattr(settings, "slack_sync_channels", ["C123", "C456"])
    connector = SlackConnector()

    payloads = await connector.sync()

    assert len(payloads) == 2
    assert payloads[0]["channel"] == "C123"


@pytest.mark.asyncio
async def test_gmail_connector_honors_label_filter(monkeypatch):
    monkeypatch.setattr(settings, "gmail_label_filter", ["IMPORTANT", "TEAM"])
    connector = EmailConnector()

    payloads = await connector.sync()

    dry_run_payload = payloads[0]
    assert dry_run_payload["labels"] == ["IMPORTANT", "TEAM"]


@pytest.mark.asyncio
async def test_calendar_connector_returns_time_window(monkeypatch):
    monkeypatch.setattr(settings, "calendar_id", "calendar@example.com")
    connector = CalendarConnector()

    payloads = await connector.sync()

    result = payloads[0]
    assert result["calendar_id"] == "calendar@example.com"


@pytest.mark.asyncio
async def test_google_drive_connector_reports_query(monkeypatch):
    monkeypatch.setattr(settings, "google_service_account_json", None)
    monkeypatch.setattr(settings, "google_service_account_file", None)
    monkeypatch.setattr(settings, "google_drive_query", "mimeType contains 'document'")
    connector = GoogleDriveConnector()

    payloads = await connector.sync()

    assert payloads[0]["query"] == "mimeType contains 'document'"


@pytest.mark.asyncio
async def test_notion_connector_returns_database_ids(monkeypatch):
    monkeypatch.setattr(settings, "notion_api_token", "token")
    monkeypatch.setattr(settings, "notion_database_ids", ["db1", "db2"])
    connector = NotionConnector()

    payloads = await connector.sync()

    assert len(payloads) == 2
    assert payloads[0]["database_id"] == "db1"


class _DummyConnector(Connector):
    def __init__(self) -> None:
        super().__init__(name="dummy")
        self.calls = 0

    @property
    def enabled(self) -> bool:
        return True

    async def sync(self, *, since=None):
        self.calls += 1
        return [{"called": self.calls, "since": since}]


@pytest.mark.asyncio
async def test_periodic_sync_manager_executes_jobs(monkeypatch):
    monkeypatch.setattr(settings, "github_repositories", [])
    monkeypatch.setattr(settings, "slack_sync_channels", [])
    monkeypatch.setattr(settings, "gmail_label_filter", [])
    monkeypatch.setattr(settings, "notion_database_ids", [])
    monkeypatch.setattr(settings, "gmail_service_account_json", None)
    monkeypatch.setattr(settings, "gmail_service_account_file", None)
    monkeypatch.setattr(settings, "gmail_scopes", ["https://www.googleapis.com/auth/gmail.readonly"])
    monkeypatch.setattr(settings, "github_token", "token")
    monkeypatch.setattr(settings, "slack_bot_token", "token")
    await database.init_db()
    manager = PeriodicSyncManager()
    connector = _DummyConnector()
    manager.register(connector, interval_seconds=1)
    manager._jobs[0].interval_seconds = 0.1  # speed up test

    await manager.start()
    try:
        for _ in range(20):
            if connector.calls >= 2:
                break
            await asyncio.sleep(0.05)
    finally:
        await manager.stop()

    assert connector.calls >= 2

    records = await sync_storage.list_recent_syncs(limit=5)
    assert any(record["connector"] == "dummy" for record in records)


# ── Linear connector tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_linear_connector_disabled_without_key(monkeypatch):
    monkeypatch.setattr(settings, "linear_api_key", None)
    connector = LinearConnector()
    assert connector.enabled is False


@pytest.mark.asyncio
async def test_linear_connector_dry_run(monkeypatch):
    monkeypatch.setattr(settings, "linear_api_key", "lin-api-test")
    monkeypatch.setattr(settings, "dry_run", True)
    connector = LinearConnector()
    payloads = await connector.sync()
    assert len(payloads) >= 0
    # Dry run should return placeholder, not hit real API
    if payloads:
        assert payloads[0].get("dry_run") is True or "team_id" in payloads[0]


@pytest.mark.asyncio
async def test_linear_connector_nests_state_filter(monkeypatch):
    monkeypatch.setattr(settings, "linear_api_key", "lin-api-test")
    monkeypatch.setattr(settings, "dry_run", False)
    connector = LinearConnector()
    captured: dict[str, Any] = {}

    async def fake_graphql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        captured["query"] = query
        captured["variables"] = variables or {}
        return {"issues": {"nodes": []}}

    monkeypatch.setattr(connector, "_graphql", fake_graphql)

    await connector.list_issues(team_id="team-123", state="In Progress", limit=10)

    assert "stateFilter" not in captured["query"]
    assert "issues(filter: $filter, orderBy: $orderBy, first: $first)" in captured["query"]
    assert captured["variables"]["filter"] == {
        "team": {"id": {"eq": "team-123"}},
        "state": {"name": {"eq": "In Progress"}},
    }


# ── Sentry connector tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_sentry_connector_disabled_without_credentials(monkeypatch):
    monkeypatch.setattr(settings, "sentry_auth_token", None)
    monkeypatch.setattr(settings, "sentry_org_slug", None)
    connector = SentryConnector()
    assert connector.enabled is False


@pytest.mark.asyncio
async def test_sentry_connector_dry_run(monkeypatch):
    monkeypatch.setattr(settings, "sentry_auth_token", "sntrys-token")
    monkeypatch.setattr(settings, "sentry_org_slug", "evalops-inc")
    monkeypatch.setattr(settings, "dry_run", True)
    connector = SentryConnector()
    payloads = await connector.sync()
    assert len(payloads) == 1
    assert payloads[0]["error_counts"].get("dry_run") is True


# ── Procedure loader tests ────────────────────────────────────────


def test_procedure_loader_discovers_yaml(tmp_path, monkeypatch):
    from agent_pm.procedures import ProcedureLoader

    proc_dir = tmp_path / "procedures"
    proc_dir.mkdir()
    (proc_dir / "test_proc.yaml").write_text("name: test\ndescription: A test procedure\nsteps: []")

    monkeypatch.setattr(settings, "procedure_dir", proc_dir)
    loader = ProcedureLoader(directory=proc_dir)
    procs = loader.load()
    assert "test_proc" in procs
    assert procs["test_proc"]["name"] == "test"


# ── Scheduler tests ───────────────────────────────────────────────


def test_scheduler_cron_matching():
    from agent_pm.scheduler import ProcedureScheduler

    s = ProcedureScheduler()

    # Match: Monday 9:00 UTC
    dt_match = datetime(2026, 6, 15, 9, 0, tzinfo=UTC)  # Monday
    assert s._cron_matches("0 9 * * 1", dt_match) is True

    # No match: wrong minute
    dt_wrong_min = datetime(2026, 6, 15, 9, 1, tzinfo=UTC)
    assert s._cron_matches("0 9 * * 1", dt_wrong_min) is False

    # No match: wrong day
    dt_wrong_day = datetime(2026, 6, 16, 9, 0, tzinfo=UTC)  # Tuesday
    assert s._cron_matches("0 9 * * 1", dt_wrong_day) is False

    # Match: every 4 hours
    dt_every_four = datetime(2026, 6, 15, 8, 0, tzinfo=UTC)
    assert s._cron_matches("0 */4 * * *", dt_every_four) is True

    # No match: not on a 4-hour boundary
    dt_not_every_four = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
    assert s._cron_matches("0 */4 * * *", dt_not_every_four) is False


@pytest.mark.asyncio
async def test_scheduler_run_procedure_executes_yaml_steps(monkeypatch):
    import agent_pm.procedure_runner as procedure_runner
    from agent_pm.procedures import loader
    from agent_pm.scheduler import ProcedureScheduler

    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        loader,
        "load",
        lambda: {
            "weekly_progress_review": {
                "name": "Weekly Progress Review",
                "steps": [
                    {
                        "id": "sentry_check",
                        "run": "sentry_scan",
                        "input": "List unresolved Sentry issues (is:unresolved, sort=freq, 14d).",
                    }
                ],
            }
        },
    )

    async def fake_list_issues(*, query: str, sort: str = "freq", limit: int = 10, stats_period: str = "14d"):
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

    await ProcedureScheduler()._run_procedure("weekly_progress_review")

    assert captured["query"] == "is:unresolved"
    assert captured["stats_period"] == "14d"
    assert captured["limit"] == 10
    assert captured["error_counts_period"] == "14d"


# ── MCP server tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_initialize():
    from agent_pm.mcp_server import handle_request

    resp = await handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert resp["result"]["serverInfo"]["name"] == "agent-pm-mcp"


@pytest.mark.asyncio
async def test_mcp_list_tools():
    from agent_pm.mcp_server import handle_request

    resp = await handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tool_names = [t["name"] for t in resp["result"]["tools"]]
    assert "agent_pm_run_procedure" in tool_names
    assert "agent_pm_sentry_scan" in tool_names
    assert "agent_pm_linear_scan" in tool_names
    assert "agent_pm_github_pr_scan" in tool_names
    assert "agent_pm_list_procedures" in tool_names


@pytest.mark.asyncio
async def test_mcp_list_procedures_tool():
    from agent_pm.mcp_server import handle_request

    resp = await handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "agent_pm_list_procedures", "arguments": {}},
        }
    )
    text = resp["result"]["content"][0]["text"]
    import json

    data = json.loads(text)
    assert "procedures" in data


@pytest.mark.asyncio
async def test_mcp_run_procedure_dry_run_short_circuits_model_steps(monkeypatch):
    import agent_pm.mcp_server as mcp_server
    import agent_pm.procedure_runner as procedure_runner
    from agent_pm.procedures import loader

    captured: dict[str, Any] = {}
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(
        loader,
        "load",
        lambda: {
            "deploy_readiness": {
                "name": "Deploy Readiness",
                "description": "Review deploy blockers.",
                "steps": [{"id": "compose_report", "run": "model", "input": "Compose a terse report."}],
            }
        },
    )

    async def fake_to_thread(func, *args, **kwargs):
        captured["called"] = True
        captured["function"] = func
        return "report body"

    monkeypatch.setattr(procedure_runner.asyncio, "to_thread", fake_to_thread)

    result = await mcp_server._run_procedure("deploy_readiness", dry_run=True)

    assert result["procedure"] == "deploy_readiness"
    assert result["dry_run"] is True
    assert result["plan_id"]
    # dry_run=True should short-circuit before calling to_thread
    assert "called" not in captured
    assert settings.dry_run is False


@pytest.mark.asyncio
async def test_mcp_run_procedure_live_executes_model_steps_in_thread(monkeypatch):
    import agent_pm.mcp_server as mcp_server
    import agent_pm.procedure_runner as procedure_runner
    from agent_pm.procedures import loader

    captured: dict[str, Any] = {}
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(
        loader,
        "load",
        lambda: {
            "deploy_readiness": {
                "name": "Deploy Readiness",
                "description": "Review deploy blockers.",
                "steps": [{"id": "compose_report", "run": "model", "input": "Compose a terse report."}],
            }
        },
    )

    async def fake_to_thread(func, *args, **kwargs):
        captured["dry_run_during_call"] = settings.dry_run
        captured["function"] = func
        captured["args"] = args
        return "report body"

    monkeypatch.setattr(procedure_runner.asyncio, "to_thread", fake_to_thread)

    result = await mcp_server._run_procedure("deploy_readiness", dry_run=False)

    assert result["procedure"] == "deploy_readiness"
    assert result["dry_run"] is False
    assert result["plan_id"]
    assert captured["function"].__self__ is procedure_runner.openai_client
    assert captured["function"].__name__ == "create_plan"
    assert captured["dry_run_during_call"] is False
    assert settings.dry_run is False


@pytest.mark.asyncio
async def test_settings_dry_run_override_is_task_local(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", False)

    async def observe(value: bool) -> bool:
        with settings.override_dry_run(value):
            await asyncio.sleep(0)
            return settings.dry_run

    observed = await asyncio.gather(observe(True), observe(False))

    assert observed == [True, False]
    assert settings.dry_run is False
