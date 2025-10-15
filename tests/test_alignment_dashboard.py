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
