from pathlib import Path

from agent_pm import alignment_export


def test_write_csv_creates_file(tmp_path):
    events = [
        {
            "event_id": "evt-1",
            "title": "Alpha",
            "notification": {"status": "success"},
            "suggestions": [{"idea": "Beta"}],
        }
    ]

    output = tmp_path / "alignments.csv"
    path = alignment_export.write_csv(output, events)

    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "evt-1" in content
    assert "Beta" in content


def test_write_csv_filters_followup(tmp_path):
    events = [
        {"event_id": "evt-1", "notification": {"status": "success"}, "followup": {"status": "ack"}},
        {"event_id": "evt-2", "notification": {"status": "success"}, "followup": {"status": "dismissed"}},
    ]

    output = tmp_path / "alignments_filtered.csv"
    alignment_export.write_csv(output, events, statuses={"ack"})

    content = output.read_text(encoding="utf-8")
    assert "evt-1" in content
    assert "evt-2" not in content


def test_build_rows_uses_flattening():
    events = [
        {
            "title": "Alpha",
            "notification": {"status": "success"},
            "suggestions": [{"idea": "Beta", "overlapping_goals": ["Improve"]}],
        }
    ]

    rows = alignment_export.build_rows(events)

    assert rows[0]["idea"] == "Beta"
