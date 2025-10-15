import app as app_module
from fastapi.testclient import TestClient


def test_alignments_endpoint(monkeypatch):
    client = TestClient(app_module.app)

    sample_events = [
        {
            "title": "Alpha",
            "context": "Context",
            "suggestions": [{"idea": "Beta", "overlapping_goals": ["Improve"], "similarity": 0.9}],
            "notification": {"status": "success"},
            "created_at": "2024-01-01T00:00:00",
        }
    ]

    async def fake_fetch(limit: int):
        assert limit == 5
        return sample_events

    monkeypatch.setattr(app_module, "fetch_alignment_events", fake_fetch)
    monkeypatch.setattr(
        app_module,
        "summarize_alignment_events",
        lambda events: {"total_events": len(events), "status_counts": {"success": 1}, "top_ideas": []},
    )

    response = client.get("/alignments?limit=5")

    assert response.status_code == 200
    payload = response.json()
    assert payload["events"] == sample_events
    assert payload["summary"]["total_events"] == 1
