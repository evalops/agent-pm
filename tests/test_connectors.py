import asyncio

import pytest

from agent_pm.connectors import (
    CalendarConnector,
    EmailConnector,
    GitHubConnector,
    GoogleDriveConnector,
    NotionConnector,
    SlackConnector,
)
from agent_pm.connectors.base import Connector
from agent_pm.settings import settings
from agent_pm.storage import database, syncs as sync_storage
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
    await asyncio.sleep(0.35)
    await manager.stop()

    assert connector.calls >= 2

    records = await sync_storage.list_recent_syncs(limit=5)
    assert any(record["connector"] == "dummy" for record in records)
