"""Planner logic for generating PRDs and ticket plans."""

import json
import logging

from .agent_sdk import CriticReview, PRDPlan, run_critic_agent, run_planner_agent
from .clients import openai_client
from .memory import TraceMemory, vector_memory
from .metrics import record_guardrail_rejection, record_planner_request, record_revisions
from .settings import settings
from .templates import PRD_TEMPLATE


def build_status_digest(
    title: str,
    context: str,
    goals: list[str],
    requirements: list[str],
    risks: list[str],
) -> str:
    goal_lines = "\n".join(f"• {item}" for item in goals)
    req_lines = "\n".join(f"- {item}" for item in requirements)
    risk_text = ", ".join(risks) if risks else "None"
    return f"*{title}*\nContext: {context or 'N/A'}\nGoals:\n{goal_lines}\nNext Steps:\n{req_lines}\nRisks: {risk_text}"


SYSTEM_PROMPT = """You are an exacting Product Manager agent.\nDeliverables:\n1) A crisp PRD (use our template).\n2) A set of tickets with summaries and ACs.\nKeep clarifying questions minimal and only when blocking. Never invent external facts."""

logger = logging.getLogger(__name__)

REVISION_LIMIT = 1


def build_user_prompt(title: str, context: str, constraints: list[str] | None = None) -> str:
    constraints = constraints or []
    constraint_text = "\n".join(f"- {c}" for c in constraints)
    return (
        f"Turn this idea into a PRD + ticket plan:\nTitle: {title}\nContext: {context}\nConstraints:\n{constraint_text}"
    )


def build_revision_prompt(
    title: str,
    context: str,
    constraints: list[str],
    plan: PRDPlan,
    review: CriticReview,
) -> str:
    plan_json = plan.model_dump_json(indent=2)
    issues = "\n".join(f"- {item}" for item in (review.issues or [])) or "- None"
    recommendations = "\n".join(f"- {item}" for item in (review.recommendations or [])) or "- None"
    return (
        "Revise the following PRD plan to address reviewer feedback. Maintain the JSON schema with"
        " keys: problem, goals, nongoals, requirements, acceptance, risks, users."
        f"\nTitle: {title}\nContext: {context}\nConstraints: {constraints}\n"
        f"Current Plan:\n{plan_json}\n"
        f"Reviewer Issues:\n{issues}\n"
        f"Reviewer Recommendations:\n{recommendations}\n"
        "Produce an improved plan that resolves the issues explicitly."
    )


def _maybe_get_dspy_guidance(title: str, context: str, constraints: list[str]) -> str:
    if not settings.use_dspy:
        return ""

    if not settings.openai_api_key:
        if settings.dry_run:
            logger.info("DSPy guidance skipped: running in dry-run mode without OPENAI_API_KEY")
        else:
            logger.warning("DSPy guidance skipped: OPENAI_API_KEY is not configured")
        return ""

    from . import dspy_program

    try:
        return dspy_program.compile_brief(title, context, constraints)
    except RuntimeError as exc:  # pragma: no cover - optional dependency path
        logger.warning("DSPy guidance disabled: %s", exc)
        return ""


def generate_plan(
    title: str,
    context: str,
    constraints: list[str] | None,
    requirements: list[str],
    acceptance: list[str],
    goals: list[str],
    nongoals: list[str],
    risks: list[str],
    users: str,
    trace: TraceMemory,
    tools: list[dict[str, object]],
    enable_tools: bool,
) -> dict[str, str]:
    constraint_list = constraints or []
    user_prompt = build_user_prompt(title, context, constraint_list)
    agent_prompt = (
        "Generate a PRD plan for the following idea. Provide structured JSON with keys: problem, "
        "goals, nongoals, requirements, acceptance, risks, users.\n"
        f"Title: {title}\nContext: {context}\nConstraints: {constraint_list}"
    )

    def _merge_list(default: list[str], candidate: list[str]) -> list[str]:
        return candidate if candidate else default

    users_value = users
    problem = "Summarized problem statement."
    plan_result: PRDPlan | None = None
    critic_review: CriticReview | None = None
    revision_history: list[dict[str, object]] = []

    prompt = agent_prompt
    conversation_base = f"planner::{title}"
    critic_base = f"critic::{title}"

    for attempt in range(1, REVISION_LIMIT + 2):
        trace.add("meta", json.dumps({"event": "planner_attempt", "attempt": attempt}))
        logger.info("planner attempt=%s enable_tools=%s", attempt, enable_tools)
        with record_planner_request():
            try:
                plan_result = run_planner_agent(
                    prompt,
                    conversation_id=f"{conversation_base}::{attempt}",
                    enable_tools=enable_tools,
                )
                trace.add("assistant", plan_result.model_dump_json())
            except ValueError as exc:
                record_guardrail_rejection("planner_input")
                logger.warning("Planner guardrail blocked request: %s", exc)
                raise
            except Exception as exc:  # pragma: no cover - planner fallback path
                logger.warning("Planner Agents SDK failed, falling back to defaults: %s", exc)
                plan_result = None

        if plan_result is None:
            break

        goals = _merge_list(goals, plan_result.goals)
        nongoals = _merge_list(nongoals, plan_result.nongoals)
        requirements = _merge_list(requirements, plan_result.requirements)
        acceptance = _merge_list(acceptance, plan_result.acceptance)
        risks = _merge_list(risks, plan_result.risks)
        users_value = plan_result.users or users
        problem = plan_result.problem or "Summarized problem statement."

        try:
            critic_review = run_critic_agent(
                plan_result,
                conversation_id=f"{critic_base}::{attempt}",
            )
            trace.add("critic", critic_review.model_dump_json())
        except Exception as exc:  # pragma: no cover - critic fallback path
            logger.warning("Critic agent failed: %s", exc)
            critic_review = None

        if not critic_review or critic_review.status != "revise":
            break
        if attempt > REVISION_LIMIT:
            break

        revision_history.append(
            {
                "attempt": attempt,
                "critic_review": critic_review.model_dump(),
                "plan": plan_result.model_dump(),
            }
        )
        trace.add(
            "meta",
            json.dumps(
                {
                    "event": "planner_revision_requested",
                    "attempt": attempt,
                    "issues": critic_review.issues,
                }
            ),
        )
        logger.info(
            "planner revision triggered attempt=%s issues=%s",
            attempt,
            "; ".join(critic_review.issues or []),
        )
        prompt = build_revision_prompt(title, context, constraint_list, plan_result, critic_review)

    guidance = _maybe_get_dspy_guidance(title, context, constraint_list)

    if guidance:
        user_prompt = f"{user_prompt}\n\nGuidance:\n{guidance}"
        logger.info("DSPy guidance appended to planner prompt")
    trace.add("user", user_prompt)
    text = openai_client.create_plan(SYSTEM_PROMPT, user_prompt, tools=tools)
    trace.add("assistant", text)
    prd = PRD_TEMPLATE.render(
        title=title,
        context=context,
        problem=problem,
        goals=goals,
        nongoals=nongoals,
        users=users_value,
        requirements=requirements,
        acceptance=acceptance,
        risks=risks,
    )
    vector_memory.record_prd(title, prd)
    digest = build_status_digest(title, context, goals, requirements, risks)
    review_payload = critic_review.model_dump() if critic_review else None
    record_revisions(len(revision_history))
    return {
        "prd_markdown": prd,
        "raw_plan": text,
        "status_digest": digest,
        "critic_review": review_payload,
        "revision_history": revision_history,
    }


__all__ = ["generate_plan", "build_user_prompt", "SYSTEM_PROMPT", "build_status_digest"]
