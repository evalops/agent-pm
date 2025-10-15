"""Planner logic for generating PRDs and ticket plans."""

import json
import logging
from collections import deque
from datetime import datetime

from . import embeddings
from .agent_sdk import CriticReview, PRDPlan, run_critic_agent, run_planner_agent
from .alignment_log import record_alignment_event
from .clients import openai_client, slack_client
from .memory import TraceMemory, vector_memory
from .metrics import (
    record_alignment_notification,
    record_dspy_guidance,
    record_guardrail_rejection,
    record_planner_request,
    record_revisions,
)
from .plugins import plugin_registry
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

GOAL_ALIGNMENT_LIMIT = 3
GOAL_ALIGNMENT_SIMILARITY_THRESHOLD = 0.7
_ALIGNMENT_HISTORY_MAX = 100
_alignment_history: deque[tuple[str, str]] = deque(maxlen=_ALIGNMENT_HISTORY_MAX)
_alignment_history_set: set[tuple[str, str]] = set()


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


def _extract_goal_section(prd_text: str) -> list[str]:
    lines = prd_text.splitlines()
    capture = False
    extracted: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            capture = stripped.lower().startswith("## goals")
            continue
        if capture and stripped.startswith("#"):
            break
        if not capture:
            continue
        goal = stripped.lstrip("-• ").strip() if stripped.startswith(("-", "•")) else stripped
        if goal:
            extracted.append(goal)
    return extracted


def _collect_related_goals(title: str, goals: list[str]) -> list[dict[str, object]]:
    if not goals:
        return []

    query_text = " ".join(goal for goal in goals if goal)
    if not query_text:
        return []

    try:
        query_embedding = embeddings.generate_embedding_sync(query_text)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("goal embedding failed: %s", exc)
        return []

    if not query_embedding:
        return []

    try:
        df = vector_memory.to_dataframe()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("vector memory unavailable: %s", exc)
        return []

    if df.empty:
        return []

    suggestions: list[dict[str, object]] = []

    for _, row in df.iterrows():
        existing_title = row.get("idea")
        if not isinstance(existing_title, str) or existing_title == title:
            continue

        prd_text = row.get("prd")
        if not isinstance(prd_text, str):
            continue

        candidate_goals = _extract_goal_section(prd_text)
        candidate_text = " ".join(candidate_goals)
        if not candidate_text:
            continue

        candidate_embedding = embeddings.generate_embedding_sync(candidate_text)
        if not candidate_embedding:
            continue

        similarity = embeddings.cosine_similarity(query_embedding, candidate_embedding)
        if similarity < GOAL_ALIGNMENT_SIMILARITY_THRESHOLD:
            continue

        candidate_text_lower = candidate_text.lower()
        overlapping = [goal for goal in goals if goal.lower() in candidate_text_lower]
        if not overlapping:
            overlapping = candidate_goals[:3]

        suggestions.append(
            {
                "idea": existing_title,
                "overlapping_goals": overlapping,
                "similarity": round(similarity, 3),
                "external_context": _build_external_context(existing_title, overlapping),
            }
        )

    suggestions.sort(key=lambda item: item.get("similarity", 0.0), reverse=True)
    return suggestions[:GOAL_ALIGNMENT_LIMIT]


def _build_alignment_note(suggestions: list[dict[str, object]]) -> str:
    lines = ["Potentially related initiatives detected:"]
    for item in suggestions:
        idea = item.get("idea", "Unknown initiative")
        goals_str = ", ".join(item.get("overlapping_goals", []))
        similarity = item.get("similarity")
        suffix = f" (similarity {similarity:.2f})" if isinstance(similarity, (int, float)) else ""
        lines.append(f"- {idea}{suffix}: {goals_str}")
    return "\n".join(lines)


def _build_external_context(idea: str, overlapping_goals: list[str]) -> dict[str, object]:
    context: dict[str, object] = {
        "recommendation": f"Coordinate with {idea} owners on goals: {', '.join(overlapping_goals)}",
    }
    if slack_client.channel:
        context["slack_channel"] = slack_client.channel
        context["slack_link_hint"] = f"https://slack.com/app_redirect?channel={slack_client.channel}"
    if settings.slack_status_channel and settings.slack_status_channel != slack_client.channel:
        context["status_channel"] = settings.slack_status_channel
    if settings.allowed_projects:
        project = settings.allowed_projects[0]
        context["jira_project"] = project
        if settings.jira_base_url:
            context["jira_search_url"] = (
                f"{settings.jira_base_url}/issues/?jql=project%3D{project}%20AND%20text~\"{idea}\""
            )
    return context


def _mark_alignment_notified(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    new_pairs: list[tuple[str, str]] = []
    for pair in pairs:
        if pair in _alignment_history_set:
            continue
        if len(_alignment_history) == _alignment_history.maxlen:
            old = _alignment_history.popleft()
            _alignment_history_set.discard(old)
        _alignment_history.append(pair)
        _alignment_history_set.add(pair)
        new_pairs.append(pair)
    return new_pairs


def _notify_alignment(
    title: str, alignment_note: str, suggestions: list[dict[str, object]]
) -> tuple[str, dict[str, object]]:
    metadata: dict[str, object] = {"message": alignment_note}
    if slack_client.channel:
        metadata["channel"] = slack_client.channel

    if not alignment_note:
        metadata["reason"] = "empty_note"
        status = "skipped"
        record_alignment_notification(status)
        return status, metadata

    if not settings.goal_alignment_notify:
        metadata["reason"] = "notifications_disabled"
        status = "disabled"
        record_alignment_notification(status)
        return status, metadata

    if not slack_client.enabled:
        if settings.dry_run:
            status = "dry_run"
            metadata["reason"] = "dry_run"
        else:
            logger.info("Slack client disabled; skipping goal alignment notification")
            status = "disabled"
            metadata["reason"] = "slack_disabled"
        record_alignment_notification(status)
        return status, metadata

    pairs = [(title, str(item.get("idea"))) for item in suggestions if item.get("idea")]
    new_pairs = _mark_alignment_notified(pairs)
    if not new_pairs:
        logger.info("Skipping duplicate goal alignment notification for %s", title)
        status = "duplicate"
        metadata["reason"] = "duplicate_pair"
        record_alignment_notification(status)
        return status, metadata

    message = f"*Goal alignment surfaced for*: {title}\n{alignment_note}"

    try:
        import asyncio

        async def _post() -> dict[str, object]:
            return await slack_client.post_digest(message)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            metadata["mode"] = "sync"
            response = asyncio.run(_post())
            metadata["response"] = response
        else:
            metadata["mode"] = "async"
            task = loop.create_task(_post())

            def _log_completion(task: asyncio.Task) -> None:
                if task.cancelled():  # pragma: no cover - defensive
                    logger.info("Goal alignment notification task cancelled")
                    record_alignment_notification("cancelled")
                elif (exc := task.exception()) is not None:
                    logger.warning("Goal alignment notification failed: %s", exc)
                    record_alignment_notification("error")
                else:
                    record_alignment_notification("success_async")

            task.add_done_callback(_log_completion)
        status = "success"
        record_alignment_notification(status)
        return status, metadata
    except Exception as exc:  # pragma: no cover - logging for observability
        logger.warning("Failed to dispatch goal alignment notification: %s", exc)
        status = "error"
        metadata["error"] = str(exc)
        record_alignment_notification(status)
        return status, metadata


def _maybe_get_dspy_guidance(title: str, context: str, constraints: list[str]) -> str:
    if not settings.use_dspy:
        record_dspy_guidance("disabled")
        return ""

    if not settings.openai_api_key:
        if settings.dry_run:
            logger.info("DSPy guidance skipped: running in dry-run mode without OPENAI_API_KEY")
        else:
            logger.warning("DSPy guidance skipped: OPENAI_API_KEY is not configured")
        record_dspy_guidance("skipped")
        return ""

    from . import dspy_program

    try:
        record_dspy_guidance("attempted")
        guidance = dspy_program.compile_brief(title, context, constraints)
        if guidance:
            record_dspy_guidance("succeeded")
        else:
            record_dspy_guidance("empty")
        return guidance
    except RuntimeError as exc:  # pragma: no cover - optional dependency path
        logger.warning("DSPy guidance disabled: %s", exc)
        record_dspy_guidance("failed")
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

    plugin_registry.fire(
        "pre_plan",
        context={
            "title": title,
            "idea_context": context,
            "constraints": list(constraint_list),
            "requirements": list(requirements),
            "acceptance": list(acceptance),
            "goals": list(goals),
            "nongoals": list(nongoals),
            "risks": list(risks),
            "users": users,
        },
        trace=trace,
        enable_tools=enable_tools,
        tools=tools,
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

    trace.add(
        "meta",
        json.dumps(
            {
                "event": "dspy_guidance",
                "status": "used" if guidance else "skipped",
            }
        ),
    )
    alignment_suggestions = _collect_related_goals(title, goals)
    alignment_status = "none"
    notification_meta: dict[str, object] = {"reason": "no_matches"}
    if alignment_suggestions:
        trace.add(
            "meta",
            json.dumps(
                {
                    "event": "goal_alignment",
                    "matches": alignment_suggestions,
                }
            ),
        )
        alignment_note = _build_alignment_note(alignment_suggestions)
        user_prompt = f"{user_prompt}\n\nExisting alignment signal:\n{alignment_note}"
        alignment_status, notification_meta = _notify_alignment(title, alignment_note, alignment_suggestions)
        logger.info("Goal alignment note appended to planner prompt")
    else:
        record_alignment_notification("none")
        notification_meta = {"reason": "no_matches"}
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
        related_initiatives=alignment_suggestions,
    )
    vector_memory.record_prd(title, prd)
    digest = build_status_digest(title, context, goals, requirements, risks)
    review_payload = critic_review.model_dump() if critic_review else None
    record_revisions(len(revision_history))
    alignment_event = record_alignment_event(
        {
            "title": title,
            "context": context,
            "timestamp": datetime.utcnow().isoformat(),
            "suggestions": alignment_suggestions,
            "notification": {"status": alignment_status, **notification_meta},
        }
    )
    result = {
        "prd_markdown": prd,
        "raw_plan": text,
        "status_digest": digest,
        "critic_review": review_payload,
        "revision_history": revision_history,
        "related_initiatives": alignment_suggestions,
        "alignment_notification": {"status": alignment_status, **notification_meta},
        "alignment_insights": alignment_suggestions,
        "alignment_event": alignment_event,
        "alignment_event_id": alignment_event.get("event_id"),
    }
    plugin_context = {
        "title": title,
        "context": context,
        "requirements": requirements,
        "acceptance": acceptance,
        "goals": goals,
        "nongoals": nongoals,
        "risks": risks,
        "users": users_value,
        "alignment": alignment_suggestions,
    }
    plugin_registry.fire("post_plan", plan=result, context=plugin_context)
    return result


__all__ = ["generate_plan", "build_user_prompt", "SYSTEM_PROMPT", "build_status_digest"]
