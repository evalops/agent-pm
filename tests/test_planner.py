import os

# Ensure required settings before importing planner
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from agent_pm import planner
from agent_pm.agent_sdk import CriticReview, PRDPlan
from agent_pm.memory import TraceMemory


class DummyOpenAIClient:
    @staticmethod
    def create_plan(*args, **kwargs):
        return "stubbed plan"


def test_generate_plan_produces_status_digest(monkeypatch):
    monkeypatch.setattr(planner, "openai_client", DummyOpenAIClient())
    monkeypatch.setattr(planner.vector_memory, "record_prd", lambda *args, **kwargs: None)
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
