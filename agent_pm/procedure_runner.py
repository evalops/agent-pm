"""Procedure execution engine for YAML-defined operational workflows."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx

from agent_pm.clients.calendar_client import calendar_client
from agent_pm.clients.jira_client import jira_client
from agent_pm.clients.openai_client import openai_client
from agent_pm.clients.slack_client import slack_client
from agent_pm.connectors.calendar import CalendarConnector
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
_NEXT_DAYS_RE = re.compile(r"next\s+(\d+)\s+days?", re.IGNORECASE)
_STALE_DAYS_RE = re.compile(r"last updated\s*>\s*(\d+)\s+days?\s+ago", re.IGNORECASE)
_EXPLICIT_PR_AUTHOR_RE = re.compile(
    r"\b(?:authored|opened)\s+by\s+([a-zA-Z0-9_.-]+)\b|\bauthor\s*:\s*([a-zA-Z0-9_.-]+)\b",
    re.IGNORECASE,
)
_KNOWN_AGENT_LOGINS = {"dependabot", "codex", "cursor", "maestro", "claude"}
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
    if settings.dry_run:
        return {"plan_id": uuid4().hex, "dry_run": True, "title": idea.title}
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
    if run_name == "calendar_scan":
        return await _run_calendar_scan(_render_text(step.get("input", ""), context))
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
    if settings.dry_run:
        return f"[dry_run] Would call model with prompt: {instruction[:200]}..."
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


async def _run_calendar_scan(instruction: str) -> dict[str, Any]:
    window_days = _extract_calendar_window_days(instruction, default=7)
    now = datetime.now(tz=UTC)
    window_start = _calendar_window_start(instruction, now)
    payloads = await CalendarConnector().sync(since=window_start)

    events: list[dict[str, Any]] = []
    if payloads and isinstance(payloads[0], dict):
        first = payloads[0]
        if first.get("dry_run") is True:
            events = []
        elif "items" in first:
            items = first.get("items") or []
            events = [event for event in items if _event_is_within_window(event, window_start, window_days)]

    return {
        "events": events,
        "count": len(events),
        "window_days": window_days,
        "calendar_id": settings.calendar_id,
    }


async def _run_linear_scan(instruction: str) -> dict[str, Any]:
    state = _extract_linear_state(instruction)
    assignee_email = _extract_linear_assignee_email(instruction)
    stale_scan = _instruction_requests_linear_stale_scan(instruction)
    oldest_updates_first = _instruction_requests_oldest_linear_updates(instruction) or stale_scan
    if _instruction_requests_assigned_to_me(instruction) and not assignee_email:
        result: dict[str, Any] = {
            "issues": [],
            "count": 0,
            "state": state,
            "assignee_email": None,
            "error": "Linear 'assigned to me' scans require JIRA_EMAIL or GOOGLE_CALENDAR_DELEGATED_USER.",
        }
        if stale_scan:
            result["stale_after_days"] = _extract_stale_days(instruction, default=2)
            result["stale"] = []
        return result

    issues = await linear_connector.list_issues(
        assignee_email=assignee_email,
        state=state,
        order_by="updatedAt",
        limit=None if oldest_updates_first else 50,
    )
    if assignee_email:
        issues = [
            issue for issue in issues if str(issue.get("assignee", {}).get("email", "")).lower() == assignee_email
        ]
    if oldest_updates_first:
        issues = sorted(issues, key=lambda issue: str(issue.get("updatedAt") or ""))

    result: dict[str, Any] = {
        "issues": issues,
        "count": len(issues),
        "state": state,
        "assignee_email": assignee_email,
    }
    if stale_scan:
        stale_after_days = _extract_stale_days(instruction, default=2)
        result["stale_after_days"] = stale_after_days
        result["stale"] = await _collect_stale_linear_issues(issues, stale_after_days=stale_after_days)
    return result


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
    author = _extract_explicit_pr_author(instruction)
    hours = _extract_last_hours(instruction)
    include_agent_authors = _instruction_requests_agent_authors(instruction)
    fetch_diffs = _instruction_requests_pr_diffs(instruction)

    if settings.dry_run or not settings.github_token or not repos:
        return {
            "dry_run": True,
            "repositories": repos,
            "author": author,
            "include_agent_authors": include_agent_authors,
            "last_hours": hours,
            "fetch_diffs": fetch_diffs,
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
                    payload = dict(pr)
                    payload["repository"] = repo
                    if fetch_diffs:
                        payload["diff"] = await _fetch_pull_request_diff(client, repo, pr, headers)
                    pulls.append(payload)

    return {
        "prs": pulls,
        "count": len(pulls),
        "repositories": repos,
        "author": author,
        "include_agent_authors": include_agent_authors,
        "last_hours": hours,
        "fetch_diffs": fetch_diffs,
    }


async def _collect_stale_linear_issues(
    issues: list[dict[str, Any]],
    *,
    stale_after_days: int,
) -> list[dict[str, Any]]:
    now = datetime.now(tz=UTC)
    stale_items = []
    for issue in issues:
        flags = []
        due = issue.get("dueDate")
        updated_at = issue.get("updatedAt")
        state_name = str(issue.get("state", {}).get("name", ""))

        if due and str(due) < now.date().isoformat():
            flags.append("past_due")
        if updated_at:
            updated_dt = _parse_datetime(str(updated_at))
            if (now - updated_dt) > timedelta(days=stale_after_days):
                flags.append("stale")
        if state_name == "In Progress":
            comments = await linear_connector.get_issue_comments(str(issue.get("id", "")), limit=20)
            if not _has_recent_comment(comments, now, stale_after_days):
                flags.append("in_progress_no_recent_comments")
        if flags:
            stale_items.append(
                {
                    "id": issue.get("id"),
                    "identifier": issue.get("identifier"),
                    "title": issue.get("title"),
                    "state": state_name,
                    "assignee": issue.get("assignee"),
                    "dueDate": due,
                    "updatedAt": updated_at,
                    "flags": flags,
                    "recommended_action": _recommend_linear_action(flags),
                }
            )
    return stale_items


async def _fetch_pull_request_diff(
    client: httpx.AsyncClient,
    repo: str,
    pr: dict[str, Any],
    headers: dict[str, str],
) -> str:
    api_diff_url = str(pr.get("url") or f"https://api.github.com/repos/{repo}/pulls/{pr.get('number')}")
    diff_url = str(pr.get("diff_url") or "")
    if not diff_url or diff_url.startswith(
        ("https://github.com/", "http://github.com/", "https://www.github.com/", "http://www.github.com/")
    ):
        diff_url = api_diff_url
    response = await client.get(
        diff_url,
        headers={**headers, "Accept": "application/vnd.github.v3.diff"},
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def _include_pull_request(
    pr: dict[str, Any],
    *,
    author: str | None,
    include_agent_authors: bool,
    last_hours: int | None,
) -> bool:
    login = str(pr.get("user", {}).get("login", ""))
    normalized_login = _normalize_github_login(login)
    if author and normalized_login != author:
        return False
    if include_agent_authors and not _is_agent_login(normalized_login):
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


def _extract_calendar_window_days(instruction: str, *, default: int) -> int:
    if "this week" in instruction.lower():
        return 7
    match = _NEXT_DAYS_RE.search(instruction)
    if match:
        return int(match.group(1))
    return default


def _calendar_window_start(instruction: str, now: datetime) -> datetime:
    if "this week" in instruction.lower():
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    return now


def _event_is_within_window(event: dict[str, Any], now: datetime, window_days: int) -> bool:
    start = event.get("start", {})
    start_value = str(start.get("dateTime") or start.get("date") or "")
    if not start_value:
        return False
    if "T" in start_value:
        start_at = _parse_datetime(start_value)
    else:
        start_at = datetime.fromisoformat(f"{start_value}T00:00:00+00:00")
    return now <= start_at <= now + timedelta(days=window_days)


def _extract_linear_state(instruction: str) -> str | None:
    lower_instruction = instruction.lower()
    if re.search(r"\b(?:state|status)\s*(?:is|=|:)\s*\"?in progress\"?\b", lower_instruction):
        return "In Progress"
    if re.search(r"\b(?:list|scan|show|pull)\s+(?:all\s+)?in progress issues\b", lower_instruction):
        return "In Progress"
    if re.search(r"\b(?:only|just)\s+in progress\b", lower_instruction):
        return "In Progress"
    return None


def _extract_linear_assignee_email(instruction: str) -> str | None:
    if not _instruction_requests_assigned_to_me(instruction):
        return None
    for email in (settings.jira_email, settings.google_calendar_delegated_user):
        if email:
            return email.lower()
    return None


def _instruction_requests_assigned_to_me(instruction: str) -> bool:
    return "assigned to me" in instruction.lower()


def _instruction_requests_oldest_linear_updates(instruction: str) -> bool:
    lower_instruction = instruction.lower()
    compact_instruction = re.sub(r"\s+", "", lower_instruction)
    return "updatedatascending" in compact_instruction or "oldest update" in lower_instruction


def _instruction_requests_linear_stale_scan(instruction: str) -> bool:
    lower_instruction = instruction.lower()
    return any(
        token in lower_instruction
        for token in ("stale", "due date", "last updated", "recent comments", "recommended actions")
    )


def _extract_stale_days(instruction: str, *, default: int) -> int:
    match = _STALE_DAYS_RE.search(instruction)
    if match:
        return int(match.group(1))
    return default


def _has_recent_comment(comments: list[dict[str, Any]], now: datetime, stale_after_days: int) -> bool:
    for comment in comments:
        created_at = comment.get("createdAt")
        if created_at and (now - _parse_datetime(str(created_at))) <= timedelta(days=stale_after_days):
            return True
    return False


def _recommend_linear_action(flags: list[str]) -> str:
    if "past_due" in flags:
        return "Update the due date or unblock the issue owner."
    if "in_progress_no_recent_comments" in flags:
        return "Request a status update or add a progress note."
    return "Review the issue and decide whether to update or close it."


def _extract_explicit_pr_author(instruction: str) -> str | None:
    match = _EXPLICIT_PR_AUTHOR_RE.search(instruction)
    if not match:
        return None
    author = match.group(1) or match.group(2)
    normalized = _normalize_github_login(author)
    if normalized in {"bot", "bots", "agent", "agents"}:
        return None
    return normalized


def _instruction_requests_agent_authors(instruction: str) -> bool:
    return bool(
        re.search(r"\b(?:authored|opened)\s+by\s+(?:bots?|agents?)(?:\s+or\s+(?:bots?|agents?))?\b", instruction, re.I)
    )


def _instruction_requests_pr_diffs(instruction: str) -> bool:
    return bool(re.search(r"\bfetch\b[^.\n]*\bdiffs?\b", instruction, re.I))


def _normalize_github_login(login: str) -> str:
    normalized = login.lower().strip()
    if normalized.endswith("[bot]"):
        normalized = normalized[:-5]
    return normalized


def _is_agent_login(login: str) -> bool:
    return login in _KNOWN_AGENT_LOGINS or login.endswith(("-bot", "_bot", "-agent", "_agent"))


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
