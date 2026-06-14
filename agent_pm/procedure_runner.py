"""Procedure execution engine for YAML-defined operational workflows."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import nullcontext
from datetime import datetime
from typing import Any
from uuid import uuid4

import httpx

from agent_pm.clients.calendar_client import calendar_client
from agent_pm.clients.jira_client import jira_client
from agent_pm.clients.openai_client import openai_client
from agent_pm.clients.slack_client import slack_client
from agent_pm.connectors.linear import linear_connector
from agent_pm.connectors.sentry import sentry_connector
from agent_pm.models import Idea, JiraIssuePayload
from agent_pm.planner import generate_plan_for_idea
from agent_pm.procedures import loader
from agent_pm.settings import settings

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"{{\s*([a-zA-Z0-9_]+)\s*}}")
_REPO_RE = re.compile(r"\b[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+\b")
_STATS_PERIOD_RE = re.compile(r"\b(\d+)([hdw])\b")
_LAST_HOURS_RE = re.compile(r"last\s+(\d+)\s+hours?", re.IGNORECASE)
_MODEL_STEP_SYSTEM_PROMPT = (
    "You are executing an operational procedure. Follow the instruction exactly, use the"
    " provided step outputs as source material, and return concise markdown or plain text."
)


async def execute_procedure(name: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Run a named procedure definition and return an execution summary."""

    procedures = loader.load()
    if name not in procedures:
        raise KeyError(name)

    proc = procedures[name]
    title = proc.get("name", name)
    plan_id = uuid4().hex
    context: dict[str, Any] = {
        "procedure": name,
        "title": title,
        "description": proc.get("description", ""),
        "plan_id": plan_id,
    }

    override = settings.override_dry_run(True) if dry_run else nullcontext()
    with override:
        steps = proc.get("steps") or []
        if not steps:
            result = await _generate_plan_result(name, proc)
            return {"procedure": name, "plan_id": result.get("plan_id", plan_id), "dry_run": dry_run}

        for step in steps:
            step_id = step.get("id") or step.get("run") or f"step_{len(context)}"
            result = await _execute_step(step, context)
            context[step_id] = result
            _store_step_aliases(step_id, result, context)

    return {"procedure": name, "plan_id": plan_id, "dry_run": dry_run}


async def _generate_plan_result(name: str, proc: dict[str, Any]) -> dict[str, Any]:
    idea = Idea(
        title=proc.get("name", name),
        context=proc.get("description", f"Execute procedure: {name}"),
    )
    return await asyncio.to_thread(generate_plan_for_idea, idea)


async def _execute_step(step: dict[str, Any], context: dict[str, Any]) -> Any:
    items_expr = step.get("foreach")
    if items_expr is None:
        return await _execute_step_once(step, context)

    items = _render_value(items_expr, context)
    if not isinstance(items, list):
        raise TypeError(
            f"Procedure step '{step.get('id', step.get('run', 'unknown'))}' expected foreach to resolve to a list"
        )

    results = []
    for item in items:
        context["item"] = item
        try:
            results.append(await _execute_step_once(step, context))
        finally:
            context.pop("item", None)
    return results


async def _execute_step_once(step: dict[str, Any], context: dict[str, Any]) -> Any:
    run_name = step.get("run")
    if not run_name:
        raise ValueError(f"Procedure step '{step.get('id', 'unknown')}' is missing a run target")

    if run_name == "sentry_scan":
        return await _run_sentry_scan(_render_text(step.get("input", ""), context))
    if run_name == "linear_scan":
        return await _run_linear_scan(_render_text(step.get("input", ""), context))
    if run_name == "github_pr_scan":
        return await _run_github_pr_scan(_render_text(step.get("input", ""), context))
    if run_name == "model":
        return await _run_model_step(_render_text(step.get("input", ""), context), context)
    if run_name == "publish_status_digest":
        params = _render_value(step.get("with", {}), context)
        return await slack_client.post_digest(str(params.get("body_md", "")), params.get("channel"))
    if run_name == "create_jira_issue":
        params = _render_value(step.get("with", {}), context)
        payload = JiraIssuePayload(
            summary=str(params.get("summary", "")),
            description=str(params.get("description", "")),
            project_key=str(params.get("project_key", "")),
            issue_type=str(params.get("issue_type", "Story")),
        )
        return await jira_client.create_issue(payload.to_jira())
    if run_name == "schedule_review_event":
        params = _render_value(step.get("with", {}), context)
        attendees = params.get("attendees") or []
        if isinstance(attendees, str):
            attendees = [email.strip() for email in attendees.split(",") if email.strip()]
        start_time = _parse_datetime(str(params.get("start_time_iso", "")))
        return await calendar_client.schedule_review(
            summary=str(params.get("summary", "")),
            description=str(params.get("description", "")),
            start_time=start_time,
            duration_minutes=int(params.get("duration_minutes", 30)),
            attendees=attendees,
        )

    raise ValueError(f"Unsupported procedure step run target: {run_name}")


async def _run_model_step(instruction: str, context: dict[str, Any]) -> str:
    step_outputs = {
        key: value
        for key, value in context.items()
        if key not in {"procedure", "title", "description", "plan_id"} and not key.startswith("_")
    }
    prompt = (
        f"Procedure: {context['title']}\n"
        f"Description: {context['description']}\n\n"
        f"Instruction:\n{instruction}\n\n"
        "Available step outputs (JSON):\n"
        f"{json.dumps(step_outputs, indent=2, default=str)}"
    )
    return await asyncio.to_thread(openai_client.create_plan, _MODEL_STEP_SYSTEM_PROMPT, prompt, [])


async def _run_sentry_scan(instruction: str) -> dict[str, Any]:
    stats_period = _extract_stats_period(instruction, default="14d")
    query = "is:unresolved" if "is:unresolved" in instruction else "is:unresolved"
    issues = await sentry_connector.list_issues(query=query, stats_period=stats_period, limit=10)
    error_counts = await sentry_connector.error_counts(stats_period=stats_period)
    return {
        "issues": issues,
        "count": len(issues),
        "query": query,
        "stats_period": stats_period,
        "error_counts": error_counts,
    }


async def _run_linear_scan(instruction: str) -> dict[str, Any]:
    state = "In Progress" if "in progress" in instruction.lower() else None
    issues = await linear_connector.list_issues(state=state, order_by="updatedAt", limit=50)
    return {"issues": issues, "count": len(issues), "state": state}


async def _run_github_pr_scan(instruction: str) -> dict[str, Any]:
    repos = (
        _extract_repositories(instruction)
        or settings.github_repositories
        or [
            "evalops/platform",
            "evalops/deploy",
            "evalops/maestro-internal",
        ]
    )
    author = "dependabot" if "dependabot" in instruction.lower() else None
    hours = _extract_last_hours(instruction)
    include_agent_authors = any(
        token in instruction.lower() for token in ("bot", "agent", "codex", "cursor", "maestro", "claude")
    )

    if settings.dry_run or not settings.github_token or not repos:
        return {
            "dry_run": True,
            "repositories": repos,
            "author": author,
            "last_hours": hours,
        }

    headers = {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
    }
    pulls: list[dict[str, Any]] = []
    async with httpx.AsyncClient() as client:
        for repo in repos:
            response = await client.get(
                f"https://api.github.com/repos/{repo}/pulls",
                headers=headers,
                params={"state": "open", "per_page": 20},
                timeout=30,
            )
            response.raise_for_status()
            for pr in response.json():
                if _include_pull_request(
                    pr, author=author, include_agent_authors=include_agent_authors, last_hours=hours
                ):
                    pulls.append(pr)

    return {"prs": pulls, "count": len(pulls), "repositories": repos, "author": author, "last_hours": hours}


def _include_pull_request(
    pr: dict[str, Any],
    *,
    author: str | None,
    include_agent_authors: bool,
    last_hours: int | None,
) -> bool:
    login = str(pr.get("user", {}).get("login", "")).lower()
    if author and author not in login:
        return False
    if include_agent_authors and not any(
        token in login for token in ("bot", "agent", "codex", "cursor", "maestro", "claude")
    ):
        return False
    if last_hours is None:
        return True

    created_at = pr.get("created_at")
    if not created_at:
        return False
    created = _parse_datetime(str(created_at))
    age = datetime.now(tz=created.tzinfo) - created
    return age.total_seconds() <= last_hours * 3600


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _extract_repositories(instruction: str) -> list[str]:
    return list(dict.fromkeys(_REPO_RE.findall(instruction)))


def _extract_stats_period(instruction: str, *, default: str) -> str:
    match = _STATS_PERIOD_RE.search(instruction)
    if match:
        return f"{match.group(1)}{match.group(2)}"
    last_hours = _extract_last_hours(instruction)
    if last_hours is not None:
        return f"{last_hours}h"
    return default


def _extract_last_hours(instruction: str) -> int | None:
    match = _LAST_HOURS_RE.search(instruction)
    if match:
        return int(match.group(1))
    return None


def _store_step_aliases(step_id: str, result: Any, context: dict[str, Any]) -> None:
    if not isinstance(result, str):
        return
    if "_" not in step_id:
        return

    alias = step_id.split("_", 1)[1]
    if alias and alias not in context:
        context[alias] = result
    if alias == "prd":
        context.setdefault("prd_md", result)


def _render_text(value: str, context: dict[str, Any]) -> str:
    rendered = _render_value(value, context)
    return rendered if isinstance(rendered, str) else _stringify_value(rendered)


def _render_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        match = _PLACEHOLDER_RE.fullmatch(value.strip())
        if match:
            return context.get(match.group(1), "")

        def _replace(found: re.Match[str]) -> str:
            return _stringify_value(context.get(found.group(1), ""))

        return _PLACEHOLDER_RE.sub(_replace, value)
    if isinstance(value, list):
        return [_render_value(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _render_value(item, context) for key, item in value.items()}
    return value


def _stringify_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(value, default=str)


__all__ = ["execute_procedure"]
