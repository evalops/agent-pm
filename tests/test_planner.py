import json
import os
from types import SimpleNamespace

import pandas as pd
import pytest

# Ensure required settings before importing planner
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from agent_pm import dspy_program, planner
from agent_pm.agent_sdk import CriticReview, PRDPlan
from agent_pm.memory import TraceMemory


class DummyOpenAIClient:
    @staticmethod
    def create_plan(*args, **kwargs):
        return "stubbed plan"


def test_generate_plan_produces_status_digest(monkeypatch):
    monkeypatch.setattr(planner, "openai_client", DummyOpenAIClient())
    monkeypatch.setattr(planner.vector_memory, "record_prd", lambda *args, **kwargs: None)
    monkeypatch.setattr(planner.vector_memory, "to_dataframe", lambda: pd.DataFrame())
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
        planner,
        "run_planner_agent",
        lambda prompt, conversation_id=None, enable_tools=False, max_turns=None: plan,
    )
    review = CriticReview(status="pass", issues=[], recommendations=["Ship weekly digest"], confidence=0.8)
    monkeypatch.setattr(
        planner,
        "run_critic_agent",
        lambda plan_result, conversation_id=None, max_turns=None: review,
    )

    result = planner.generate_plan(
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
        tools=[],
        enable_tools=False,
    )

    assert result["prd_markdown"].startswith("# PRD: Test Initiative")
    assert "status_digest" in result
    assert "*Test Initiative*" in result["status_digest"]
    assert "stubbed plan" in result["raw_plan"]
    assert result["critic_review"]["status"] == "pass"
    assert result["revision_history"] == []


def test_generate_plan_revision_flow(monkeypatch):
    monkeypatch.setattr(planner, "openai_client", DummyOpenAIClient())
    monkeypatch.setattr(planner.vector_memory, "record_prd", lambda *args, **kwargs: None)
    monkeypatch.setattr(planner.vector_memory, "to_dataframe", lambda: pd.DataFrame())

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

    monkeypatch.setattr(planner, "run_planner_agent", fake_planner)

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
        planner,
        "run_critic_agent",
        lambda plan_result, conversation_id=None, max_turns=None: next(review_iter),
    )

    result = planner.generate_plan(
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
        tools=[],
        enable_tools=False,
    )

    assert result["critic_review"]["status"] == "pass"
    assert len(result["revision_history"]) == 1
    assert result["revision_history"][0]["critic_review"]["status"] == "revise"
    assert "Activation metric defined" in result["prd_markdown"]


def test_generate_plan_appends_dspy_guidance(monkeypatch):
    monkeypatch.setattr(planner.settings, "use_dspy", True)
    monkeypatch.setattr(planner.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(planner.settings, "dry_run", False)
    monkeypatch.setattr(planner.vector_memory, "record_prd", lambda *args, **kwargs: None)
    monkeypatch.setattr(planner.vector_memory, "to_dataframe", lambda: pd.DataFrame())

    plan = PRDPlan(
        problem="Goal misalignment",
        goals=["Align stakeholders"],
        nongoals=[],
        requirements=["Schedule sync"],
        acceptance=["Sync held"],
        risks=["Scheduling conflicts"],
        users="PM team",
    )
    monkeypatch.setattr(
        planner,
        "run_planner_agent",
        lambda prompt, conversation_id=None, enable_tools=False, max_turns=None: plan,
    )
    monkeypatch.setattr(
        planner,
        "run_critic_agent",
        lambda plan_result, conversation_id=None, max_turns=None: CriticReview(
            status="pass", issues=[], recommendations=[], confidence=0.9
        ),
    )

    guidance_text = "Prioritize stakeholder interviews."

    def _fake_compile_brief(*args, **kwargs):
        return guidance_text

    dspy_program._configured_program.cache_clear()
    monkeypatch.setattr(dspy_program, "compile_brief", _fake_compile_brief)

    recorded_outcomes: list[str] = []

    monkeypatch.setattr(planner, "record_dspy_guidance", lambda outcome: recorded_outcomes.append(outcome))

    captured_prompt = {}

    def _fake_create_plan(system_prompt, user_prompt, tools):
        captured_prompt["user"] = user_prompt
        return "generated plan"

    monkeypatch.setattr(planner, "openai_client", SimpleNamespace(create_plan=_fake_create_plan))

    trace = TraceMemory()

    result = planner.generate_plan(
        title="Stakeholder Visibility",
        context="Need shared goals",
        constraints=["Complete within two weeks"],
        requirements=["Draft charter"],
        acceptance=["Charter approved"],
        goals=["Improve alignment"],
        nongoals=["Rebuild tooling"],
        risks=["Time constraints"],
        users="PMs",
        trace=trace,
        tools=[],
        enable_tools=False,
    )

    assert guidance_text in captured_prompt["user"]
    assert result["raw_plan"] == "generated plan"
    assert recorded_outcomes == ["attempted", "succeeded"]
    guidance_events = [json.loads(e["content"]) for e in trace.dump() if e["role"] == "meta"]
    assert {"event": "dspy_guidance", "status": "used"} in guidance_events


def test_generate_plan_handles_dspy_runtime_error(monkeypatch):
    monkeypatch.setattr(planner.settings, "use_dspy", True)
    monkeypatch.setattr(planner.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(planner.settings, "dry_run", False)
    monkeypatch.setattr(planner.vector_memory, "record_prd", lambda *args, **kwargs: None)
    monkeypatch.setattr(planner.vector_memory, "to_dataframe", lambda: pd.DataFrame())

    plan = PRDPlan(
        problem="Missing metrics",
        goals=["Define KPIs"],
        nongoals=[],
        requirements=["Collect baseline"],
        acceptance=["KPIs documented"],
        risks=["Data gaps"],
        users="Analytics team",
    )
    monkeypatch.setattr(
        planner,
        "run_planner_agent",
        lambda prompt, conversation_id=None, enable_tools=False, max_turns=None: plan,
    )
    monkeypatch.setattr(
        planner,
        "run_critic_agent",
        lambda plan_result, conversation_id=None, max_turns=None: CriticReview(
            status="pass", issues=[], recommendations=[], confidence=0.9
        ),
    )
    def _failing_compile_brief(*args, **kwargs):
        raise RuntimeError("DSPy offline")

    dspy_program._configured_program.cache_clear()
    monkeypatch.setattr(dspy_program, "compile_brief", _failing_compile_brief)

    recorded_outcomes: list[str] = []
    monkeypatch.setattr(planner, "record_dspy_guidance", lambda outcome: recorded_outcomes.append(outcome))

    captured_prompt = {}

    def _fake_create_plan(system_prompt, user_prompt, tools):
        captured_prompt["user"] = user_prompt
        return "plan without guidance"

    monkeypatch.setattr(planner, "openai_client", SimpleNamespace(create_plan=_fake_create_plan))

    trace = TraceMemory()

    result = planner.generate_plan(
        title="Analytics Revamp",
        context="Need KPI baseline",
        constraints=["Launch in Q2"],
        requirements=["Document KPIs"],
        acceptance=["KPI deck shared"],
        goals=["Improve measurement"],
        nongoals=["Rebuild data warehouse"],
        risks=["Data quality"],
        users="Analytics",
        trace=trace,
        tools=[],
        enable_tools=False,
    )

    assert "DSPy offline" not in captured_prompt["user"]
    assert "Guidance" not in captured_prompt["user"]
    assert result["raw_plan"] == "plan without guidance"
    assert recorded_outcomes == ["attempted", "failed"]
    guidance_events = [json.loads(e["content"]) for e in trace.dump() if e["role"] == "meta"]
    assert {"event": "dspy_guidance", "status": "skipped"} in guidance_events


def test_generate_plan_skips_guidance_when_disabled(monkeypatch):
    monkeypatch.setattr(planner.settings, "use_dspy", False)
    monkeypatch.setattr(planner.vector_memory, "record_prd", lambda *args, **kwargs: None)
    monkeypatch.setattr(planner.vector_memory, "to_dataframe", lambda: pd.DataFrame())

    plan = PRDPlan(
        problem="General problem",
        goals=["Goal"],
        nongoals=[],
        requirements=["Requirement"],
        acceptance=["Acceptance"],
        risks=["Risk"],
        users="User",
    )
    monkeypatch.setattr(
        planner,
        "run_planner_agent",
        lambda prompt, conversation_id=None, enable_tools=False, max_turns=None: plan,
    )
    monkeypatch.setattr(
        planner,
        "run_critic_agent",
        lambda plan_result, conversation_id=None, max_turns=None: CriticReview(
            status="pass", issues=[], recommendations=[], confidence=1.0
        ),
    )

    recorded_outcomes: list[str] = []
    monkeypatch.setattr(planner, "record_dspy_guidance", lambda outcome: recorded_outcomes.append(outcome))

    trace = TraceMemory()

    monkeypatch.setattr(planner, "openai_client", SimpleNamespace(create_plan=lambda *args, **kwargs: "plan"))

    planner.generate_plan(
        title="No DSPy",
        context="",
        constraints=None,
        requirements=["Requirement"],
        acceptance=["Acceptance"],
        goals=["Goal"],
        nongoals=[],
        risks=["Risk"],
        users="User",
        trace=trace,
        tools=[],
        enable_tools=False,
    )

    assert recorded_outcomes == ["disabled"]
    guidance_events = [json.loads(e["content"]) for e in trace.dump() if e["role"] == "meta"]
    assert {"event": "dspy_guidance", "status": "skipped"} in guidance_events


def test_generate_plan_skips_guidance_without_api_key(monkeypatch):
    monkeypatch.setattr(planner.settings, "use_dspy", True)
    monkeypatch.setattr(planner.settings, "openai_api_key", None)
    monkeypatch.setattr(planner.settings, "dry_run", False)
    monkeypatch.setattr(planner.vector_memory, "record_prd", lambda *args, **kwargs: None)
    monkeypatch.setattr(planner.vector_memory, "to_dataframe", lambda: pd.DataFrame())

    plan = PRDPlan(
        problem="General problem",
        goals=["Goal"],
        nongoals=[],
        requirements=["Requirement"],
        acceptance=["Acceptance"],
        risks=["Risk"],
        users="User",
    )
    monkeypatch.setattr(
        planner,
        "run_planner_agent",
        lambda prompt, conversation_id=None, enable_tools=False, max_turns=None: plan,
    )
    monkeypatch.setattr(
        planner,
        "run_critic_agent",
        lambda plan_result, conversation_id=None, max_turns=None: CriticReview(
            status="pass", issues=[], recommendations=[], confidence=1.0
        ),
    )

    recorded_outcomes: list[str] = []
    monkeypatch.setattr(planner, "record_dspy_guidance", lambda outcome: recorded_outcomes.append(outcome))

    trace = TraceMemory()

    monkeypatch.setattr(planner, "openai_client", SimpleNamespace(create_plan=lambda *args, **kwargs: "plan"))

    planner.generate_plan(
        title="Missing Key",
        context="",
        constraints=None,
        requirements=["Requirement"],
        acceptance=["Acceptance"],
        goals=["Goal"],
        nongoals=[],
        risks=["Risk"],
        users="User",
        trace=trace,
        tools=[],
        enable_tools=False,
    )

    assert recorded_outcomes == ["skipped"]
    guidance_events = [json.loads(e["content"]) for e in trace.dump() if e["role"] == "meta"]
    assert {"event": "dspy_guidance", "status": "skipped"} in guidance_events


def test_goal_alignment_appends_note(monkeypatch):
    monkeypatch.setattr(planner.settings, "use_dspy", False)
    monkeypatch.setattr(planner.vector_memory, "record_prd", lambda *args, **kwargs: None)

    alignment_df = pd.DataFrame(
        [
            {
                "idea": "Visibility OKRs",
                "prd": "# PRD\n## Goals\n- Improve visibility for PMs\n- Increase adoption",
            }
        ]
    )
    monkeypatch.setattr(planner.vector_memory, "to_dataframe", lambda: alignment_df)

    monkeypatch.setattr(
        planner.embeddings,
        "generate_embedding_sync",
        lambda text, model="text-embedding-3-small": [1.0, 0.0]
        if "visibility" in text.lower()
        else [0.0, 1.0],
    )
    monkeypatch.setattr(planner.embeddings, "cosine_similarity", lambda a, b: 0.95 if a == b else 0.1)

    planner._alignment_history.clear()
    planner._alignment_history_set.clear()

    plan = PRDPlan(
        problem="Data fragmentation",
        goals=["Improve visibility for PMs"],
        nongoals=[],
        requirements=["Ship dashboard"],
        acceptance=["Dashboard live"],
        risks=["Adoption"],
        users="PM org",
    )

    monkeypatch.setattr(planner, "run_planner_agent", lambda *args, **kwargs: plan)
    monkeypatch.setattr(
        planner,
        "run_critic_agent",
        lambda *args, **kwargs: CriticReview(status="pass", issues=[], recommendations=[], confidence=0.9),
    )

    captured_prompt = {}
    notifications: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        planner,
        "_notify_alignment",
        lambda *args, **kwargs: notifications.append((args, kwargs)),
    )

    def _fake_create_plan(system_prompt, user_prompt, tools):
        captured_prompt["user"] = user_prompt
        return "plan"

    monkeypatch.setattr(planner, "openai_client", SimpleNamespace(create_plan=_fake_create_plan))

    trace = TraceMemory()

    planner.generate_plan(
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
        tools=[],
        enable_tools=False,
    )

    assert "Existing alignment signal:" in captured_prompt["user"]
    assert "Visibility OKRs" in captured_prompt["user"]
    alignment_events = [json.loads(e["content"]) for e in trace.dump() if e["role"] == "meta"]
    matching_events = [e for e in alignment_events if e.get("event") == "goal_alignment"]
    assert matching_events
    assert notifications and notifications[0][0][0] == "Visibility Initiative"


def test_notify_alignment_respects_configuration(monkeypatch):
    planner._alignment_history.clear()
    planner._alignment_history_set.clear()

    monkeypatch.setattr(planner.settings, "goal_alignment_notify", False)
    monkeypatch.setattr(planner.settings, "dry_run", False)
    monkeypatch.setattr(planner.slack_client, "token", "token", raising=False)
    monkeypatch.setattr(planner.slack_client, "channel", "channel", raising=False)

    calls: list[str] = []

    async def _fake_post(body_md: str, channel: str | None = None) -> dict[str, object]:
        calls.append(body_md)
        return {"ok": True}

    monkeypatch.setattr(planner.slack_client, "post_digest", _fake_post)

    planner._notify_alignment("Test Initiative", "Note", [{"idea": "Other"}])

    assert calls == []


def test_notify_alignment_deduplicates_pairs(monkeypatch):
    planner._alignment_history.clear()
    planner._alignment_history_set.clear()

    monkeypatch.setattr(planner.settings, "goal_alignment_notify", True)
    monkeypatch.setattr(planner.settings, "dry_run", False)
    monkeypatch.setattr(planner.slack_client, "token", "token", raising=False)
    monkeypatch.setattr(planner.slack_client, "channel", "channel", raising=False)

    calls: list[str] = []

    async def _fake_post(body_md: str, channel: str | None = None) -> dict[str, object]:
        calls.append(body_md)
        return {"ok": True}

    monkeypatch.setattr(planner.slack_client, "post_digest", _fake_post)

    planner._notify_alignment("Test Initiative", "Note", [{"idea": "Other"}])
    planner._notify_alignment("Test Initiative", "Another note", [{"idea": "Other"}])

    assert len(calls) == 1
