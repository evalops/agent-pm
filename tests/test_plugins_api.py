from fastapi.testclient import TestClient

from agent_pm import auth
from agent_pm.auth import AdminKeyDep, APIKeyDep
from agent_pm.plugins import plugin_registry
from agent_pm.settings import settings
from app import app


def test_plugins_listing_endpoint(monkeypatch):
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


def test_feedback_submission_and_listing(monkeypatch, tmp_path):
    auth.settings = settings
    app.dependency_overrides[APIKeyDep] = lambda: "test"
    app.dependency_overrides[AdminKeyDep] = lambda: "test"
    client = TestClient(app)
    plugin = plugin_registry.get("feedback_collector")
    assert plugin is not None

    original_path = plugin.storage_path
    plugin.storage_path = tmp_path / "feedback.json"
    plugin.storage_path.write_text("[]", encoding="utf-8")
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
    finally:
        plugin.storage_path = original_path
        app.dependency_overrides.clear()
