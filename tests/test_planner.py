import json
import os

import pandas as pd
import pytest

# Ensure required settings before importing planner
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from agent_pm import planner as planner_module
from agent_pm.agent_sdk import CriticReview, PRDPlan
from agent_pm.memory import TraceMemory


@pytest.fixture(autouse=True)
def capture_alignment_events(monkeypatch):
    events: list[dict[str, object]] = []

    def _record(event: dict[str, object]) -> dict[str, object]:
        payload = dict(event)
        payload.setdefault("event_id", f"evt-{len(events)}")
        events.append(payload)
        return payload

    monkeypatch.setattr(planner_module, "record_alignment_event", _record)
    return events


def test_generate_plan_produces_status_digest(monkeypatch):
    monkeypatch.setattr(planner_module.vector_memory, "record_prd", lambda *args, **kwargs: None)
    monkeypatch.setattr(planner_module.vector_memory, "to_dataframe", lambda: pd.DataFrame())
    fired_hooks: list[str] = []
    monkeypatch.setattr(
        planner_module.plugin_registry,
        "fire",
        lambda hook, *args, **kwargs: fired_hooks.append(hook),
    )
    plan = PRDPlan(
        problem="Lack of visibility",
        goals=["Improve transparency"],
        nongoals=["Rewrite systems"],
        requirements=["Ship dashboards"],
        acceptance=["Dashboard visible"],
        risks=["Scope creep"],
        users="PMs and execs",
    )
    monkeypatch.setattr(
        planner_module,
        "run_planner_agent",
        lambda prompt, conversation_id=None, enable_tools=False, max_turns=None: plan,
    )
    review = CriticReview(status="pass", issues=[], recommendations=["Ship weekly digest"], confidence=0.8)
    monkeypatch.setattr(
        planner_module,
        "run_critic_agent",
        lambda plan_result, conversation_id=None, max_turns=None: review,
    )

    result = planner_module.generate_plan(
        title="Test Initiative",
        context="Need visibility",
        constraints=["Two-week MVP"],
        requirements=["Deliver dashboard"],
        acceptance=["AC1"],
        goals=["Improve visibility"],
        nongoals=["Rebuild infra"],
        risks=["Scope creep"],
        users="PMs",
        trace=TraceMemory(),
        enable_tools=False,
    )

    assert result["prd_markdown"].startswith("# PRD: Test Initiative")
    assert result["plan_id"]
    assert "status_digest" in result
    assert "*Test Initiative*" in result["status_digest"]
    assert result["critic_review"]["status"] == "pass"
    assert result["revision_history"] == []
    assert "pre_plan" in fired_hooks
    assert "post_plan" in fired_hooks


def test_generate_plan_revision_flow(monkeypatch):
    monkeypatch.setattr(planner_module.vector_memory, "record_prd", lambda *args, **kwargs: None)
    monkeypatch.setattr(planner_module.vector_memory, "to_dataframe", lambda: pd.DataFrame())

    first_plan = PRDPlan(
        problem="Ambiguous scope",
        goals=["Improve onboarding"],
        nongoals=[],
        requirements=["Draft outline"],
        acceptance=["Outline shared"],
        risks=["Low adoption"],
        users="New customers",
    )
    second_plan = PRDPlan(
        problem="Ambiguous scope",
        goals=["Improve onboarding", "Measure activation"],
        nongoals=[],
        requirements=["Draft outline", "Instrument activation funnel"],
        acceptance=["Outline shared", "Activation metric defined"],
        risks=["Low adoption"],
        users="New customers",
    )

    plan_iter = iter([first_plan, second_plan])

    def fake_planner(prompt, conversation_id=None, enable_tools=False, max_turns=None):
        return next(plan_iter)

    monkeypatch.setattr(planner_module, "run_planner_agent", fake_planner)

    review_iter = iter(
        [
            CriticReview(
                status="revise",
                issues=["Acceptance criteria lack measurable targets"],
                recommendations=["Add quantitative activation goal"],
                confidence=0.4,
            ),
            CriticReview(status="pass", issues=[], recommendations=[], confidence=0.9),
        ]
    )

    monkeypatch.setattr(
        planner_module,
        "run_critic_agent",
        lambda plan_result, conversation_id=None, max_turns=None: next(review_iter),
    )

    result = planner_module.generate_plan(
        title="Onboarding Revamp",
        context="Activation is flat",
        constraints=["Ship in Q1"],
        requirements=["Baseline instrumentation"],
        acceptance=["Activation baseline captured"],
        goals=["Improve activation"],
        nongoals=["Rebuild billing"],
        risks=["Engineering bandwidth"],
        users="Growth PMs",
        trace=TraceMemory(),
        enable_tools=False,
    )

    assert result["critic_review"]["status"] == "pass"
    assert len(result["revision_history"]) == 1
    assert result["revision_history"][0]["critic_review"]["status"] == "revise"
    assert "Activation metric defined" in result["prd_markdown"]


def test_goal_alignment_surfaces_related_initiatives(monkeypatch, capture_alignment_events):
    monkeypatch.setattr(planner_module.vector_memory, "record_prd", lambda *args, **kwargs: None)

    alignment_df = pd.DataFrame(
        [
            {
                "idea": "Visibility OKRs",
                "prd": "# PRD\n## Goals\n- Improve visibility for PMs\n- Increase adoption",
            }
        ]
    )
    monkeypatch.setattr(planner_module.vector_memory, "to_dataframe", lambda: alignment_df)

    monkeypatch.setattr(
        planner_module.embeddings,
        "generate_embedding_sync",
        lambda text, model="text-embedding-3-small": [1.0, 0.0] if "visibility" in text.lower() else [0.0, 1.0],
    )
    monkeypatch.setattr(planner_module.embeddings, "cosine_similarity", lambda a, b: 0.95 if a == b else 0.1)

    planner_module._alignment_history.clear()
    planner_module._alignment_history_set.clear()

    plan = PRDPlan(
        problem="Data fragmentation",
        goals=["Improve visibility for PMs"],
        nongoals=[],
        requirements=["Ship dashboard"],
        acceptance=["Dashboard live"],
        risks=["Adoption"],
        users="PM org",
    )

    monkeypatch.setattr(planner_module, "run_planner_agent", lambda *args, **kwargs: plan)
    monkeypatch.setattr(
        planner_module,
        "run_critic_agent",
        lambda *args, **kwargs: CriticReview(status="pass", issues=[], recommendations=[], confidence=0.9),
    )

    notifications: list[tuple[tuple[str, ...], dict[str, object]]] = []
    recorded_statuses: list[str] = []
    monkeypatch.setattr(
        planner_module,
        "record_alignment_notification",
        lambda status: recorded_statuses.append(status),
    )

    def _fake_notify(*args, **kwargs):
        notifications.append((args, kwargs))
        planner_module.record_alignment_notification("success")
        return "success", {"channel": "test"}

    monkeypatch.setattr(planner_module, "_notify_alignment", _fake_notify)

    trace = TraceMemory()

    result = planner_module.generate_plan(
        title="Visibility Initiative",
        context="Need better dashboards",
        constraints=["Launch this quarter"],
        requirements=["Dashboard"],
        acceptance=["Usage tracked"],
        goals=["Improve visibility for PMs"],
        nongoals=[],
        risks=["Bandwidth"],
        users="PM org",
        trace=trace,
        enable_tools=False,
    )

    alignment_events = [json.loads(e["content"]) for e in trace.dump() if e["role"] == "meta"]
    matching_events = [e for e in alignment_events if e.get("event") == "goal_alignment"]
    assert matching_events
    assert notifications and notifications[0][0][0] == "Visibility Initiative"

    assert result["related_initiatives"]
    assert result["alignment_insights"] == result["related_initiatives"]
    assert result["alignment_notification"]["status"] == "success"
    assert "## Related Initiatives" in result["prd_markdown"]
    assert recorded_statuses.count("success") >= 1
    assert capture_alignment_events
    assert capture_alignment_events[-1]["notification"]["status"] == "success"
    assert result["alignment_event_id"]


def test_notify_alignment_respects_configuration(monkeypatch):
    planner_module._alignment_history.clear()
    planner_module._alignment_history_set.clear()

    monkeypatch.setattr(planner_module.settings, "goal_alignment_notify", False)
    monkeypatch.setattr(planner_module.settings, "dry_run", False)
    monkeypatch.setattr(planner_module.slack_client, "token", "token", raising=False)
    monkeypatch.setattr(planner_module.slack_client, "channel", "channel", raising=False)

    calls: list[str] = []

    async def _fake_post(body_md: str, channel: str | None = None) -> dict[str, object]:
        calls.append(body_md)
        return {"ok": True}

    monkeypatch.setattr(planner_module.slack_client, "post_digest", _fake_post)

    statuses: list[str] = []
    monkeypatch.setattr(planner_module, "record_alignment_notification", lambda status: statuses.append(status))

    status, meta = planner_module._notify_alignment("Test Initiative", "Note", [{"idea": "Other"}])

    assert calls == []
    assert status == "disabled"
    assert "disabled" in statuses
    assert meta.get("reason") == "notifications_disabled"


def test_notify_alignment_deduplicates_pairs(monkeypatch):
    planner_module._alignment_history.clear()
    planner_module._alignment_history_set.clear()

    monkeypatch.setattr(planner_module.settings, "goal_alignment_notify", True)
    monkeypatch.setattr(planner_module.settings, "dry_run", False)
    monkeypatch.setattr(planner_module.slack_client, "token", "token", raising=False)
    monkeypatch.setattr(planner_module.slack_client, "channel", "channel", raising=False)

    calls: list[str] = []

    async def _fake_post(body_md: str, channel: str | None = None) -> dict[str, object]:
        calls.append(body_md)
        return {"ok": True}

    monkeypatch.setattr(planner_module.slack_client, "post_digest", _fake_post)

    statuses: list[str] = []
    monkeypatch.setattr(planner_module, "record_alignment_notification", lambda status: statuses.append(status))

    first_status, first_meta = planner_module._notify_alignment("Test Initiative", "Note", [{"idea": "Other"}])
    second_status, second_meta = planner_module._notify_alignment(
        "Test Initiative", "Another note", [{"idea": "Other"}]
    )

    assert len(calls) == 1
    assert first_status == "success"
    assert second_status == "duplicate"
    assert "duplicate" in statuses
    assert second_meta.get("reason") == "duplicate_pair"
