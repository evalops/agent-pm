"""Integration with OpenAI Agents SDK for planning."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

import yaml
from agents import (
    Agent,
    FunctionTool,
    GuardrailFunctionOutput,
    InputGuardrailTripwireTriggered,
    Runner,
    SQLiteSession,
    function_tool,
    input_guardrail,
    output_guardrail,
    trace,
)
from pydantic import BaseModel, Field, field_validator

from .clients import jira_client, slack_client
from .models import JiraIssuePayload
from .observability.metrics import record_tool_invocation
from .settings import settings

logger = logging.getLogger(__name__)


class PRDPlan(BaseModel):
    problem: str = Field(default="Summarized problem statement.")
    goals: list[str] = Field(default_factory=list)
    nongoals: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    users: str = Field(default="Engineers, PMs, stakeholders")

    @field_validator("goals", "nongoals", "requirements", "acceptance", "risks", mode="before")
    @staticmethod
    def _ensure_list(value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            return [item.strip() for item in value.splitlines() if item.strip()]
        return []


class CriticReview(BaseModel):
    status: Literal["pass", "revise"] = Field(default="pass")
    issues: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("issues", "recommendations", mode="before")
    @staticmethod
    def _ensure_list(value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            return [item.strip() for item in value.splitlines() if item.strip()]
        return []


SUSPICIOUS_PATTERNS = (
    "ignore previous instructions",
    "forget the system prompt",
    "sudo rm -rf",
    "wipe all data",
)
MAX_PROMPT_CHARS = 6000


def _assess_prompt(prompt: str) -> GuardrailFunctionOutput:
    prompt_lower = prompt.lower()
    issues: list[str] = []

    if len(prompt) > MAX_PROMPT_CHARS:
        issues.append(f"prompt too long ({len(prompt)} chars)")
    for pattern in SUSPICIOUS_PATTERNS:
        if pattern in prompt_lower:
            issues.append(f"contains disallowed pattern: '{pattern}'")

    return GuardrailFunctionOutput(
        output_info={"issues": issues, "length": len(prompt)},
        tripwire_triggered=bool(issues),
    )


@input_guardrail(name="check_prompt_safety")
async def ensure_safe_prompt(context, agent, input_data) -> GuardrailFunctionOutput:
    prompt = input_data if isinstance(input_data, str) else "\n".join(str(item) for item in input_data)  # type: ignore[arg-type]
    return _assess_prompt(prompt)


def _validate_jira_inputs(summary: str, description: str, project_key: str) -> None:
    issues: list[str] = []
    if not summary or not summary.strip():
        issues.append("summary missing")
    if len(summary) > 255:
        issues.append("summary too long")
    if not description or len(description.strip()) < 10:
        issues.append("description too short")
    if not project_key or not project_key.strip():
        issues.append("project key missing")
    if issues:
        raise ValueError(f"Invalid Jira issue payload: {', '.join(issues)}")


def _validate_slack_digest(body_md: str, channel: str | None) -> None:
    issues: list[str] = []
    if not body_md or not body_md.strip():
        issues.append("body empty")
    if len(body_md) > 4000:
        issues.append("body exceeds 4000 characters")
    if channel is not None and not channel.strip():
        issues.append("channel invalid")
    if issues:
        raise ValueError(f"Invalid Slack digest payload: {', '.join(issues)}")


@function_tool(name_override="create_jira_issue")
async def tool_create_jira_issue(
    summary: str,
    description: str,
    project_key: str,
    issue_type: str = "Story",
) -> str:
    """Create a Jira issue using the configured client (respects DRY_RUN)."""

    try:
        _validate_jira_inputs(summary, description, project_key)
    except ValueError:
        record_tool_invocation("create_jira_issue", "validation_error")
        raise
    logger.info(
        "tool_create_jira_issue summary_len=%s project=%s issue_type=%s",
        len(summary),
        project_key,
        issue_type,
    )
    payload = JiraIssuePayload(
        project_key=project_key,
        summary=summary,
        description=description,
        issue_type=issue_type,
    )
    try:
        result = await jira_client.create_issue(payload.to_jira())
    except Exception:
        record_tool_invocation("create_jira_issue", "error")
        raise
    record_tool_invocation("create_jira_issue", "success")
    return json.dumps(result)


@function_tool(name_override="post_slack_digest")
async def tool_post_slack_digest(body_md: str, channel: str | None = None) -> str:
    """Post a Slack status digest (respects DRY_RUN)."""

    try:
        _validate_slack_digest(body_md, channel)
    except ValueError:
        record_tool_invocation("post_slack_digest", "validation_error")
        raise
    logger.info(
        "tool_post_slack_digest body_len=%s channel=%s",
        len(body_md),
        channel or settings.slack_status_channel,
    )
    try:
        result = await slack_client.post_digest(body_md, channel)
    except Exception:
        record_tool_invocation("post_slack_digest", "error")
        raise
    record_tool_invocation("post_slack_digest", "success")
    return json.dumps(result)


TOOLS: list[FunctionTool] = [tool_create_jira_issue, tool_post_slack_digest]


DEFAULT_PLANNER_INSTRUCTIONS = (
    "You are a senior product manager. When given an idea title, context, and constraints,\n"
    "respond with a JSON object matching the provided schema. Prioritize clarity, include\n"
    "explicit non-goals, and propose concrete requirements, acceptance criteria, risks, and\n"
    "users. Use tools when necessary to format outputs or prepare follow-up actions."
)

DEFAULT_CRITIC_INSTRUCTIONS = (
    "You are a principal product operations reviewer. Given a PRD plan as JSON, assess its"
    " completeness, strength of acceptance criteria, clarity of requirements, and risk"
    " coverage. Respond as JSON with keys: status (pass or revise), issues (list of"
    " problems), recommendations (list of actionable improvements), confidence"
    " (0-1 float). Be concise and actionable."
)

DEFAULT_PLANNER_CONFIG: dict[str, Any] = {
    "name": "Agent PM Planner",
    "model": "gpt-4.1-mini",
    "instructions": DEFAULT_PLANNER_INSTRUCTIONS,
    "max_turns": 4,
    "enable_tools_by_default": False,
}

DEFAULT_CRITIC_CONFIG: dict[str, Any] = {
    "name": "Agent PM Critic",
    "model": "gpt-4.1-mini",
    "instructions": DEFAULT_CRITIC_INSTRUCTIONS,
    "max_turns": 4,
}


@output_guardrail(name="require_prd_sections")
async def ensure_prd_completeness(context, agent, output: PRDPlan) -> GuardrailFunctionOutput:
    missing_sections: list[str] = []
    if not output.goals:
        missing_sections.append("goals")
    if not output.requirements:
        missing_sections.append("requirements")
    if not output.acceptance:
        missing_sections.append("acceptance")
    if not output.risks:
        missing_sections.append("risks")

    breakdown = {
        "missing_sections": missing_sections,
        "users": output.users,
    }

    if missing_sections:
        return GuardrailFunctionOutput(output_info=breakdown, tripwire_triggered=True)


_BASE_PLANNER_AGENT: Agent
_PLANNER_AGENT_WITH_TOOLS: Agent
_CRITIC_AGENT: Agent
_planner_default_max_turns: int = DEFAULT_PLANNER_CONFIG["max_turns"]
_critic_default_max_turns: int = DEFAULT_CRITIC_CONFIG["max_turns"]
_planner_tools_default: bool = DEFAULT_PLANNER_CONFIG["enable_tools_by_default"]

_RUNNER = Runner()


def _load_agent_config_file() -> dict[str, Any]:
    path = settings.agents_config_path
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - invalid config
        logger.warning("Failed to parse agents config: %s", exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("Agents config must be a mapping; falling back to defaults")
        return {}
    return data


def reload_agent_profiles() -> None:
    global _BASE_PLANNER_AGENT, _PLANNER_AGENT_WITH_TOOLS, _CRITIC_AGENT
    global _planner_default_max_turns, _critic_default_max_turns, _planner_tools_default

    config = _load_agent_config_file()

    planner_section = config.get("planner")
    planner_cfg = (
        DEFAULT_PLANNER_CONFIG | planner_section if isinstance(planner_section, dict) else DEFAULT_PLANNER_CONFIG
    )

    critic_section = config.get("critic")
    critic_cfg = DEFAULT_CRITIC_CONFIG | critic_section if isinstance(critic_section, dict) else DEFAULT_CRITIC_CONFIG

    _planner_default_max_turns = int(planner_cfg.get("max_turns", DEFAULT_PLANNER_CONFIG["max_turns"]))
    _critic_default_max_turns = int(critic_cfg.get("max_turns", DEFAULT_CRITIC_CONFIG["max_turns"]))
    _planner_tools_default = bool(
        planner_cfg.get("enable_tools_by_default", DEFAULT_PLANNER_CONFIG["enable_tools_by_default"])
    )

    _BASE_PLANNER_AGENT = Agent(
        name=planner_cfg.get("name", DEFAULT_PLANNER_CONFIG["name"]),
        instructions=planner_cfg.get("instructions", DEFAULT_PLANNER_CONFIG["instructions"]),
        model=planner_cfg.get("model", DEFAULT_PLANNER_CONFIG["model"]),
        output_type=PRDPlan,
        output_guardrails=[ensure_prd_completeness],
        input_guardrails=[ensure_safe_prompt],
    )

    _PLANNER_AGENT_WITH_TOOLS = _BASE_PLANNER_AGENT.clone(tools=TOOLS)

    _CRITIC_AGENT = Agent(
        name=critic_cfg.get("name", DEFAULT_CRITIC_CONFIG["name"]),
        instructions=critic_cfg.get("instructions", DEFAULT_CRITIC_CONFIG["instructions"]),
        model=critic_cfg.get("model", DEFAULT_CRITIC_CONFIG["model"]),
        output_type=CriticReview,
    )


def planner_tools_default_enabled() -> bool:
    return _planner_tools_default


def _prepare_session(conversation_id: str) -> SQLiteSession:
    session_path: Path = settings.agent_session_db
    session_path.parent.mkdir(parents=True, exist_ok=True)
    return SQLiteSession(conversation_id, str(session_path))


def _build_critic_prompt(plan: PRDPlan) -> str:
    plan_json = plan.model_dump_json(indent=2)
    return (
        "Review the following PRD plan. Identify any missing sections, weak acceptance criteria, "
        "or unclear requirements. Respond in JSON with: status, issues, recommendations, "
        "confidence.\n"
        f"Plan:\n{plan_json}"
    )


def run_critic_agent(
    plan: PRDPlan,
    conversation_id: str | None = None,
    max_turns: int | None = None,
) -> CriticReview:
    session = _prepare_session(conversation_id or "planner-critic")
    prompt = _build_critic_prompt(plan)
    try:
        with trace("critic-agent"):
            result = _RUNNER.run_sync(
                _CRITIC_AGENT,
                prompt,
                session=session,
                max_turns=max_turns or _critic_default_max_turns,
            )
    except Exception as exc:  # pragma: no cover - safety net
        logger.warning("Critic agent failed: %s", exc)
        raise

    final_output = getattr(result, "final_output", None)
    if isinstance(final_output, CriticReview):
        return final_output
    if hasattr(final_output, "model_dump"):
        return CriticReview.model_validate(final_output.model_dump())
    if isinstance(final_output, dict):
        return CriticReview.model_validate(final_output)
    if isinstance(final_output, str):
        try:
            return CriticReview.model_validate_json(final_output)
        except Exception:  # pragma: no cover - fallback
            logger.warning("Critic agent returned unparsable string output")
    logger.warning("Critic agent response missing structured output; using defaults")
    return CriticReview(status="revise", issues=["Critic returned no structured output."])


def run_planner_agent(
    prompt: str,
    conversation_id: str | None = None,
    max_turns: int | None = None,
    enable_tools: bool | None = None,
) -> PRDPlan:
    """Execute the planner agent and return a structured PRD plan."""

    preliminary = _assess_prompt(prompt)
    if preliminary.tripwire_triggered:
        issues = preliminary.output_info.get("issues", []) if isinstance(preliminary.output_info, dict) else []
        reason = ", ".join(issues) if issues else "input rejected by guardrail"
        raise ValueError(f"Planner input rejected: {reason}")

    session = _prepare_session(conversation_id or "planner")
    try:
        with trace("planner-agent"):
            tools_flag = _planner_tools_default if enable_tools is None else enable_tools
            agent = _PLANNER_AGENT_WITH_TOOLS if tools_flag else _BASE_PLANNER_AGENT
            result = _RUNNER.run_sync(
                agent,
                prompt,
                session=session,
                max_turns=max_turns or _planner_default_max_turns,
            )
    except InputGuardrailTripwireTriggered as exc:
        issues = _assess_prompt(prompt).output_info.get("issues", [])
        reason = ", ".join(issues) if issues else "input rejected by guardrail"
        logger.warning("Planner guardrail triggered: %s", reason)
        raise ValueError(f"Planner input rejected: {reason}") from exc
    except Exception as exc:  # pragma: no cover - safety net for runtime failures
        logger.warning("Agents SDK call failed: %s", exc)
        raise

    final_output = getattr(result, "final_output", None)
    if isinstance(final_output, PRDPlan):
        return final_output
    if hasattr(final_output, "model_dump"):
        return PRDPlan.model_validate(final_output.model_dump())
    if isinstance(final_output, dict):
        return PRDPlan.model_validate(final_output)
    if isinstance(final_output, str):
        try:
            return PRDPlan.model_validate_json(final_output)
        except Exception:  # pragma: no cover - fallback to defaults
            logger.warning("Planner agent returned unparsable string output")
    logger.warning("Planner agent response missing structured output; using defaults")
    return PRDPlan()


reload_agent_profiles()


__all__ = [
    "run_planner_agent",
    "run_critic_agent",
    "reload_agent_profiles",
    "planner_tools_default_enabled",
    "PRDPlan",
    "CriticReview",
]
