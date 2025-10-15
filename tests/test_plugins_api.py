from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_pm import auth
from agent_pm.auth import AdminKeyDep, APIKeyDep
from agent_pm.plugins import plugin_registry
from agent_pm.settings import settings
from app import app


@pytest.fixture
def plugin_config_tmp(monkeypatch, tmp_path):
    original_path = plugin_registry.path
    temp_config = tmp_path / "plugins.yaml"
    source_path = settings.plugin_config_path
    temp_config.write_text(Path(source_path).read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(plugin_registry, "path", temp_config)
    plugin_registry.reload()
    yield temp_config
    monkeypatch.setattr(plugin_registry, "path", original_path)
    plugin_registry.reload()


def test_plugins_listing_endpoint(monkeypatch, plugin_config_tmp):
    auth.settings = settings  # ensure auth uses default settings
    app.dependency_overrides[APIKeyDep] = lambda: "test"
    app.dependency_overrides[AdminKeyDep] = lambda: "test"
    client = TestClient(app)
    response = client.get("/plugins")
    assert response.status_code == 200
    payload = response.json()
    names = {item["name"] for item in payload.get("plugins", [])}
    assert "ticket_automation" in names
    assert "feedback_collector" in names
    app.dependency_overrides.clear()


def test_feedback_submission_and_listing(monkeypatch, tmp_path, plugin_config_tmp):
    auth.settings = settings
    app.dependency_overrides[APIKeyDep] = lambda: "test"
    app.dependency_overrides[AdminKeyDep] = lambda: "test"
    client = TestClient(app)
    plugin = plugin_registry.get("feedback_collector")
    assert plugin is not None

    original_path = plugin.storage_path
    plugin.storage_path = tmp_path / "feedback.json"
    plugin.storage_path.write_text("[]", encoding="utf-8")
    fired: list[tuple[str, dict[str, Any]]] = []
    original_fire = plugin_registry.fire

    def capture_fire(hook: str, *args, **kwargs):
        fired.append((hook, kwargs))
        return original_fire(hook, *args, **kwargs)

    monkeypatch.setattr(plugin_registry, "fire", capture_fire)
    try:
        response = client.post(
            "/plugins/feedback",
            json={"title": "Plan A", "rating": 5, "comment": "Great", "source": "test"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Plan A"

        list_response = client.get("/plugins/feedback")
        assert list_response.status_code == 200
        records = list_response.json()["feedback"]
        assert len(records) == 1
        assert records[0]["rating"] == 5
        assert any(hook == "on_feedback" for hook, _ in fired)
    finally:
        plugin.storage_path = original_path
        app.dependency_overrides.clear()


def test_plugin_toggle_endpoints(monkeypatch, plugin_config_tmp):
    auth.settings = settings
    app.dependency_overrides[APIKeyDep] = lambda: "test"
    app.dependency_overrides[AdminKeyDep] = lambda: "test"
    client = TestClient(app)

    enable_resp = client.post("/plugins/slack_followup_alerts/enable")
    assert enable_resp.status_code == 200
    assert enable_resp.json()["plugin"]["enabled"] is True

    disable_resp = client.post("/plugins/slack_followup_alerts/disable")
    assert disable_resp.status_code == 200
    assert disable_resp.json()["plugin"]["enabled"] is False

    reload_resp = client.post("/plugins/reload")
    assert reload_resp.status_code == 200
    names = {item["name"] for item in reload_resp.json().get("plugins", [])}
    assert "slack_followup_alerts" in names

    app.dependency_overrides.clear()


def test_plugin_config_update_endpoint(monkeypatch, tmp_path, plugin_config_tmp):
    auth.settings = settings
    app.dependency_overrides[APIKeyDep] = lambda: "test"
    app.dependency_overrides[AdminKeyDep] = lambda: "test"
    client = TestClient(app)

    new_path = str(tmp_path / "custom.jsonl")
    response = client.post(
        "/plugins/warehouse_export/config",
        json={"config": {"path": new_path}},
    )
    assert response.status_code == 200
    assert response.json()["plugin"]["config"]["path"] == new_path

    metadata = plugin_registry.metadata_for("warehouse_export")
    assert metadata["config"]["path"] == new_path

    app.dependency_overrides.clear()
