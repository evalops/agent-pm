import asyncio

from agent_pm import alignment_log


def test_summarize_alignment_events_counts():
    events = [
        {"notification": {"status": "success"}, "suggestions": [{"idea": "Alpha"}, {"idea": "Beta"}]},
        {"notification": {"status": "duplicate"}, "suggestions": [{"idea": "Alpha"}]},
        {"notification": {"status": "error"}, "suggestions": []},
    ]

    summary = alignment_log.summarize_alignment_events(events)

    assert summary["total_events"] == 3
    assert summary["status_counts"]["success"] == 1
    assert summary["status_counts"]["duplicate"] == 1
    assert summary["status_counts"]["error"] == 1
    assert summary["top_ideas"][0][0] == "Alpha"


def test_get_alignment_summary(monkeypatch):
    async def fake_fetch(limit: int):
        return [{"notification": {"status": "success"}, "suggestions": []}]

    monkeypatch.setattr(alignment_log, "fetch_alignment_events", fake_fetch)

    events, summary = alignment_log.get_alignment_summary(limit=5)

    assert len(events) == 1
    assert summary["status_counts"]["success"] == 1


def test_record_alignment_event_assigns_id(tmp_path, monkeypatch):
    temp_log = alignment_log.AlignmentLog(tmp_path / "alignments.json")
    monkeypatch.setattr(alignment_log, "_alignment_log", temp_log)
    monkeypatch.setattr(alignment_log, "_database_configured", lambda: False)

    event = alignment_log.record_alignment_event({"title": "Alpha", "notification": {"status": "success"}})

    assert event["event_id"]
    stored = temp_log.load()
    assert stored[0]["event_id"] == event["event_id"]


def test_record_alignment_followup_event_updates_log(tmp_path, monkeypatch):
    temp_log = alignment_log.AlignmentLog(tmp_path / "alignments.json")
    monkeypatch.setattr(alignment_log, "_alignment_log", temp_log)
    monkeypatch.setattr(alignment_log, "_database_configured", lambda: False)

    event = alignment_log.record_alignment_event({"title": "Alpha", "notification": {"status": "success"}})

    updated = asyncio.run(alignment_log.record_alignment_followup_event(event["event_id"], "ack"))

    assert updated is True
    stored = temp_log.load()[0]
    assert stored["followup"]["status"] == "ack"
