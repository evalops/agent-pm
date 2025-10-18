import json

from agent_pm.memory import TraceMemory
from agent_pm.settings import settings
from agent_pm.observability.traces import list_traces, persist_trace, summarize_trace


def test_list_and_summarize_traces(tmp_path, monkeypatch):
    original_dir = settings.trace_dir
    monkeypatch.setattr(settings, "trace_dir", tmp_path)

    trace = TraceMemory()
    trace.add("meta", json.dumps({"event": "planner_attempt", "attempt": 1}))
    trace.add("meta", json.dumps({"event": "planner_revision_requested", "attempt": 1}))
    trace.add("critic", json.dumps({"status": "pass"}))

    path = persist_trace("Test Plan", trace)

    entries = list_traces()
    assert entries[0]["name"] == path.name

    summary = summarize_trace(path.name)
    assert summary["attempts"] == 1
    assert summary["revisions"] == 1
    assert summary["critic_status"] == "pass"

    monkeypatch.setattr(settings, "trace_dir", original_dir)
