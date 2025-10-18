from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_pm.api import auth
from agent_pm.api.auth import AdminKeyDep, APIKeyDep
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
    for item in payload.get("plugins", []):
        assert "errors" in item
        assert "secrets" in item
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
        assert any(record.get("title") == "Plan A" for record in records)
        assert records[-1]["rating"] == 5
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
    assert "errors" in response.json()["plugin"]

    metadata = plugin_registry.metadata_for("warehouse_export")
    assert metadata["config"]["path"] == new_path

    app.dependency_overrides.clear()


def test_plugins_discover_endpoint(monkeypatch):
    auth.settings = settings
    app.dependency_overrides[APIKeyDep] = lambda: "test"
    app.dependency_overrides[AdminKeyDep] = lambda: "test"
    client = TestClient(app)

    monkeypatch.setattr(
        plugin_registry,
        "discover_plugins",
        lambda: [{"entry_point": "demo", "module": "demo:Plugin", "plugin_name": "demo"}],
    )

    response = client.get("/plugins/discover")
    assert response.status_code == 200
    payload = response.json()
    assert payload["entry_points"][0]["entry_point"] == "demo"

    app.dependency_overrides.clear()


def test_plugins_reload_plugin_endpoint(monkeypatch):
    auth.settings = settings
    app.dependency_overrides[APIKeyDep] = lambda: "test"
    app.dependency_overrides[AdminKeyDep] = lambda: "test"
    client = TestClient(app)

    called: dict[str, Any] = {}

    def fake_reload(name: str):
        called["name"] = name
        return {"name": name, "enabled": True, "config": {}, "hook_stats": {}}

    monkeypatch.setattr(plugin_registry, "reload_plugin", fake_reload)

    response = client.post("/plugins/demo/reload")
    assert response.status_code == 200
    assert called["name"] == "demo"

    app.dependency_overrides.clear()


def test_plugins_install_endpoint_from_entry_point(monkeypatch):
    auth.settings = settings
    app.dependency_overrides[APIKeyDep] = lambda: "test"
    app.dependency_overrides[AdminKeyDep] = lambda: "test"
    client = TestClient(app)

    monkeypatch.setattr(
        plugin_registry,
        "discover_plugins",
        lambda: [
            {
                "entry_point": "demo",
                "module": "agent_pm.plugins.warehouse_export:WarehouseExportPlugin",
                "plugin_name": "warehouse_export",
                "description": "Demo plugin",
                "hooks": ["post_ticket_export"],
            }
        ],
    )

    captured: dict[str, Any] = {}

    def fake_install(module_ref: str, **kwargs: Any) -> dict[str, Any]:
        captured["module"] = module_ref
        captured["kwargs"] = kwargs
        return {
            "name": kwargs.get("name", "warehouse_export"),
            "enabled": kwargs.get("enabled", False),
            "config": kwargs.get("config", {}),
        }

    monkeypatch.setattr(plugin_registry, "install_plugin", fake_install)

    response = client.post(
        "/plugins/install",
        json={
            "entry_point": "demo",
            "enabled": True,
            "config": {"path": "./demo.jsonl"},
        },
    )
    assert response.status_code == 200
    assert captured["module"] == "agent_pm.plugins.warehouse_export:WarehouseExportPlugin"
    assert captured["kwargs"]["enabled"] is True
    assert captured["kwargs"]["config"]["path"] == "./demo.jsonl"
    assert response.json()["plugin"]["name"] == "warehouse_export"

    app.dependency_overrides.clear()
