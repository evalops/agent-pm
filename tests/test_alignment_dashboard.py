from agent_pm import alignment_dashboard


def test_load_alignment_data_uses_api(monkeypatch):
    monkeypatch.setenv("ALIGNMENTS_API_URL", "http://example/api")
    monkeypatch.setenv("ALIGNMENTS_API_KEY", "secret")

    def fake_fetch(url, key, limit):
        assert url == "http://example/api"
        assert key == "secret"
        assert limit == 25
        return ([{"notification": {"status": "success"}}], {"total_events": 1, "status_counts": {"success": 1}})

    monkeypatch.setattr(alignment_dashboard, "fetch_from_api", fake_fetch)
    events, summary, source = alignment_dashboard.load_alignment_data(limit=25)

    assert events and source == "api"
    assert summary["total_events"] == 1


def test_load_alignment_data_fallback(monkeypatch):
    monkeypatch.setenv("ALIGNMENTS_API_URL", "http://example/api")

    def failing_fetch(url, key, limit):
        raise RuntimeError("boom")

    monkeypatch.setattr(alignment_dashboard, "fetch_from_api", failing_fetch)
    monkeypatch.setattr(
        alignment_dashboard,
        "get_alignment_summary",
        lambda limit: ([{"notification": {"status": "success"}}], {"total_events": 1, "status_counts": {"success": 1}}),
    )

    events, summary, source = alignment_dashboard.load_alignment_data(limit=10)

    assert source == "local"
    assert summary["total_events"] == 1


def test_flatten_alignment_records_builds_rows():
    events = [
        {
            "title": "Alpha",
            "created_at": "2024-01-01T00:00:00",
            "notification": {"status": "success", "channel": "pm-alerts"},
            "suggestions": [
                {
                    "idea": "Beta",
                    "overlapping_goals": ["Improve"],
                    "similarity": 0.9,
                    "external_context": {"slack_link_hint": "https://slack"},
                }
            ],
        }
    ]

    records = alignment_dashboard.flatten_alignment_records(events)

    assert len(records) == 1
    assert records[0]["channel"] == "pm-alerts"
    assert records[0]["slack_link"] == "https://slack"


def test_status_trend_by_day_groups_counts():
    events = [
        {"created_at": "2024-01-01T00:00:00", "notification": {"status": "success"}},
        {"created_at": "2024-01-01T01:00:00", "notification": {"status": "error"}},
        {"created_at": "2024-01-02T01:00:00", "notification": {"status": "success"}},
    ]

    trend = alignment_dashboard.status_trend_by_day(events)

    assert trend[0]["success"] == 1
    assert trend[0]["error"] == 1
    assert trend[1]["success"] == 1


def test_status_counts_by_idea_breakdown():
    events = [
        {
            "notification": {"status": "success"},
            "suggestions": [{"idea": "Beta"}, {"idea": "Gamma"}],
        },
        {
            "notification": {"status": "error"},
            "suggestions": [{"idea": "Beta"}],
        },
    ]

    breakdown = alignment_dashboard.status_counts_by_idea(events)

    assert breakdown[0]["idea"] == "Beta"
    assert breakdown[0]["success"] == 1
    assert breakdown[0]["error"] == 1
