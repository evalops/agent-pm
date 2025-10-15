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
