"""FastAPI service exposing Agent PM planner and ticket flows."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from agent_pm.agent_sdk import planner_tools_default_enabled, reload_agent_profiles
from agent_pm.auth import AdminKeyDep, APIKeyDep
from agent_pm.clients import calendar_client, github_client, jira_client, slack_client
from agent_pm.database import PRDVersion, get_db
from agent_pm.guardrails import guardrail_context, rate_limited
from agent_pm.health import check_all_dependencies
from agent_pm.logging_config import configure_logging
from agent_pm.memory import TraceMemory
from agent_pm.metrics import latest_metrics
from agent_pm.models import BatchIdea, Idea, JiraIssuePayload, ReviewEvent, SlackDigest, TicketPlan
from agent_pm.planner import generate_plan
from agent_pm.prd_changelog import generate_changelog
from agent_pm.prd_versions import approve_version, create_branch, create_version, get_blame, get_version_history
from agent_pm.procedures import loader as procedure_loader
from agent_pm.rate_limit import enforce_concurrency_limit, enforce_rate_limit, release_concurrency
from agent_pm.settings import settings
from agent_pm.structured_logging import configure_structured_logging, get_correlation_id, set_correlation_id
from agent_pm.task_queue import TaskQueue, TaskStatus
from agent_pm.tools import registry
from agent_pm.trace_export import schedule_trace_export
from agent_pm.traces import list_traces as list_trace_files
from agent_pm.traces import persist_trace, summarize_trace

if settings.log_format == "json":
    configure_structured_logging()
else:
    configure_logging(settings.trace_dir)

logger = logging.getLogger(__name__)
app = FastAPI(title="Agent PM", version="0.1.0")
_jira_lock = asyncio.Lock()
_task_queue: TaskQueue | None = None


@app.on_event("startup")
async def startup_event():
    global _task_queue
    _task_queue = TaskQueue(max_workers=settings.task_queue_workers)
    _task_queue.start()
    logger.info("Agent PM service started")


@app.on_event("shutdown")
async def shutdown_event():
    if _task_queue:
        await _task_queue.stop()
    logger.info("Agent PM service stopped")


async def ensure_project_allowed(plan: TicketPlan) -> TicketPlan:
    allowed = settings.allowed_projects
    if allowed and plan.project_key not in allowed:
        raise HTTPException(status_code=403, detail="Project not allowed")
    return plan


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "dry_run": str(guardrail_context.dry_run)}


@app.get("/health/ready")
async def health_ready() -> dict[str, Any]:
    """Deep health check: verifies all critical dependencies."""
    result = await check_all_dependencies()
    return result


@app.get("/procedures")
async def procedures() -> dict[str, Any]:
    return procedure_loader.load()


@app.post("/plan", dependencies=[Depends(enforce_rate_limit), Depends(enforce_concurrency_limit)])
async def plan(idea: Idea, _api_key: APIKeyDep = None) -> dict[str, Any]:
    set_correlation_id(str(uuid.uuid4()))
    logger.info("Plan request received", extra={"title": idea.title, "correlation_id": get_correlation_id()})
    try:
        return await _plan_impl(idea)
    finally:
        release_concurrency()


async def _plan_impl(idea: Idea) -> dict[str, Any]:
    trace = TraceMemory()
    defaults = {
        "requirements": [
            "Generate PRD using standard template",
            "Create Jira epics and stories automatically",
            "Publish Slack status digest",
        ],
        "acceptance": [
            "PRD includes context, goals, non-goals, ACs",
            "Ticket plan generated with action items",
            "Status digest ready for stakeholders",
        ],
        "goals": ["Ship MVP", "Lower time-to-spec", "Reduce PM toil"],
        "nongoals": ["Rewrite infrastructure"],
        "risks": ["Hallucinated scope", "Missed dependency"],
        "users": "Engineers, PMs, stakeholders",
    }
    default_tool_flag = settings.agent_tools_enabled or planner_tools_default_enabled()
    enable_tools = default_tool_flag if idea.enable_tools is None else idea.enable_tools
    response_tools = registry.as_openai_tools() if enable_tools else []

    try:
        result = generate_plan(
            title=idea.title,
            context=idea.context or "",
            constraints=idea.constraints,
            requirements=defaults["requirements"],
            acceptance=defaults["acceptance"],
            goals=defaults["goals"],
            nongoals=defaults["nongoals"],
            risks=defaults["risks"],
            users=defaults["users"],
            trace=trace,
            tools=response_tools,
            enable_tools=bool(enable_tools),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    trace_path = persist_trace(idea.title, trace)
    logger.info("Trace stored at %s", trace_path)
    result["trace_name"] = trace_path.name
    # Schedule async export to external systems
    schedule_trace_export(trace_path)
    return result


@app.post("/plan/batch", dependencies=[Depends(enforce_rate_limit)])
async def plan_batch(batch: BatchIdea, _api_key: APIKeyDep = None) -> dict[str, Any]:
    """Plan multiple ideas in parallel."""
    tasks = []
    for idea in batch.ideas:
        tasks.append(_plan_impl(idea))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    plans = []
    errors = []
    for idx, result in enumerate(results):
        if isinstance(result, Exception):
            errors.append({"index": idx, "title": batch.ideas[idx].title, "error": str(result)})
        else:
            plans.append(result)

    return {"plans": plans, "errors": errors, "total": len(batch.ideas)}


@app.post("/ticket")
async def ticket(plan: TicketPlan = Depends(ensure_project_allowed), _api_key: APIKeyDep = None) -> dict[str, Any]:
    created: list[Any] = []
    async with rate_limited(_jira_lock):
        for story in plan.stories:
            payload = JiraIssuePayload(
                project_key=plan.project_key,
                summary=story,
                description=f"{story}\n\nAuto-created by Agent PM",
            )
            record = await jira_client.create_issue(payload.to_jira())
            created.append(record)
    return {"created": created}


@app.post("/github/project-note")
async def github_project_note(project_id: str, note: str) -> dict[str, Any]:
    record = await github_client.add_project_note(project_id, note)
    return record


@app.post("/slack/status")
async def slack_status(payload: SlackDigest) -> dict[str, Any]:
    record = await slack_client.post_digest(payload.body_md, payload.channel)
    return record


@app.post("/calendar/review")
async def schedule_review(event: ReviewEvent) -> dict[str, Any]:
    try:
        start = datetime.fromisoformat(event.start_time_iso)
    except ValueError as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=400, detail="Invalid start_time_iso") from exc
    record = await calendar_client.schedule_review(
        summary=event.summary,
        description=event.description,
        start_time=start,
        duration_minutes=event.duration_minutes,
        attendees=event.attendees,
    )
    return record


@app.post("/admin/reload-agents")
async def reload_agents(_admin_key: AdminKeyDep = None) -> dict[str, str]:
    reload_agent_profiles()
    return {"status": "reloaded"}


@app.get("/operators/traces")
async def operator_list_traces(limit: int = 5, _admin_key: AdminKeyDep = None) -> dict[str, Any]:
    try:
        entries = list_trace_files(limit)
    except ValueError as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"traces": entries}


@app.get("/operators/traces/{trace_name}")
async def operator_get_trace(
    trace_name: str, include_events: bool = False, _admin_key: AdminKeyDep = None
) -> dict[str, Any]:
    try:
        summary = summarize_trace(trace_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Trace not found: {trace_name}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not include_events:
        summary = {k: v for k, v in summary.items() if k != "events"}
    return summary


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    content = latest_metrics()
    return PlainTextResponse(content, media_type="text/plain; version=0.0.4")


@app.get("/tasks/{task_id}")
async def get_task(task_id: str, _admin_key: AdminKeyDep = None) -> dict[str, Any]:
    """Get task status by ID."""
    if not _task_queue:
        raise HTTPException(status_code=503, detail="Task queue not initialized")
    task = await _task_queue.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "task_id": task.task_id,
        "name": task.name,
        "status": task.status.value,
        "created_at": task.created_at.isoformat(),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "retry_count": task.retry_count,
        "error": task.error,
    }


@app.get("/tasks")
async def list_tasks(status: str | None = None, limit: int = 50, _admin_key: AdminKeyDep = None) -> dict[str, Any]:
    """List all tasks with optional status filter."""
    if not _task_queue:
        raise HTTPException(status_code=503, detail="Task queue not initialized")
    task_status = TaskStatus(status) if status else None
    tasks = await _task_queue.list_tasks(status=task_status, limit=limit)
    return {
        "tasks": [
            {
                "task_id": t.task_id,
                "name": t.name,
                "status": t.status.value,
                "created_at": t.created_at.isoformat(),
                "retry_count": t.retry_count,
                "error": t.error,
            }
            for t in tasks
        ],
        "total": len(tasks),
    }


@app.post("/prd/{plan_id}/versions")
async def create_prd_version(
    plan_id: str,
    prd_markdown: str,
    commit_message: str | None = None,
    author: str | None = None,
    author_email: str | None = None,
    parent_version_id: str | None = None,
    branch: str = "main",
    db: AsyncSession = Depends(get_db),
    _api_key: APIKeyDep = None,
) -> dict[str, Any]:
    """Create new PRD version (git commit)."""
    version = await create_version(
        session=db,
        plan_id=plan_id,
        prd_markdown=prd_markdown,
        author=author,
        author_email=author_email,
        commit_message=commit_message,
        parent_version_id=parent_version_id,
        branch=branch,
    )
    return {
        "version_id": version.version_id,
        "plan_id": version.plan_id,
        "branch": version.branch,
        "commit_message": version.commit_message,
        "author": version.author,
        "diff_summary": version.diff_summary,
        "created_at": version.created_at.isoformat(),
    }


@app.get("/prd/{plan_id}/versions")
async def list_prd_versions(
    plan_id: str,
    branch: str = "main",
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _api_key: APIKeyDep = None,
) -> dict[str, Any]:
    """Get PRD version history (git log)."""
    versions = await get_version_history(db, plan_id, branch, limit)
    return {
        "plan_id": plan_id,
        "branch": branch,
        "versions": [
            {
                "version_id": v.version_id,
                "commit_message": v.commit_message,
                "author": v.author,
                "status": v.status,
                "diff_summary": v.diff_summary,
                "created_at": v.created_at.isoformat(),
            }
            for v in versions
        ],
        "total": len(versions),
    }


@app.get("/prd/{plan_id}/versions/{version_id}")
async def get_prd_version(
    plan_id: str,
    version_id: str,
    db: AsyncSession = Depends(get_db),
    _api_key: APIKeyDep = None,
) -> dict[str, Any]:
    """Get specific PRD version."""
    from sqlalchemy import select

    result = await db.execute(select(PRDVersion).where(PRDVersion.version_id == version_id))
    version = result.scalar_one_or_none()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    return {
        "version_id": version.version_id,
        "plan_id": version.plan_id,
        "branch": version.branch,
        "prd_markdown": version.prd_markdown,
        "commit_message": version.commit_message,
        "author": version.author,
        "author_email": version.author_email,
        "status": version.status,
        "diff_summary": version.diff_summary,
        "parent_version_id": version.parent_version_id,
        "created_at": version.created_at.isoformat(),
    }


@app.post("/prd/{plan_id}/branches")
async def create_prd_branch(
    plan_id: str,
    source_version_id: str,
    branch_name: str,
    author: str | None = None,
    db: AsyncSession = Depends(get_db),
    _api_key: APIKeyDep = None,
) -> dict[str, Any]:
    """Create new branch (git checkout -b)."""
    version = await create_branch(db, plan_id, source_version_id, branch_name, author)
    return {
        "version_id": version.version_id,
        "plan_id": version.plan_id,
        "branch": version.branch,
        "created_at": version.created_at.isoformat(),
    }


@app.get("/prd/{plan_id}/blame/{version_id}")
async def get_prd_blame(
    plan_id: str,
    version_id: str,
    db: AsyncSession = Depends(get_db),
    _api_key: APIKeyDep = None,
) -> dict[str, Any]:
    """Get blame info (who wrote which sections)."""
    blame = await get_blame(db, version_id)
    return {"version_id": version_id, "plan_id": plan_id, "blame": blame}


@app.post("/prd/{plan_id}/versions/{version_id}/approve")
async def approve_prd_version(
    plan_id: str,
    version_id: str,
    reviewer: str,
    comments: str | None = None,
    db: AsyncSession = Depends(get_db),
    _admin_key: AdminKeyDep = None,
) -> dict[str, Any]:
    """Approve PRD version (PR approval)."""
    version = await approve_version(db, version_id, reviewer, comments)
    return {
        "version_id": version.version_id,
        "status": version.status,
        "message": "Version approved",
    }


@app.get("/prd/{plan_id}/changelog/{from_version}/{to_version}")
async def get_prd_changelog(
    plan_id: str,
    from_version: str,
    to_version: str,
    db: AsyncSession = Depends(get_db),
    _api_key: APIKeyDep = None,
) -> dict[str, Any]:
    """Generate changelog between two versions."""
    from sqlalchemy import select

    # Fetch both versions
    result = await db.execute(select(PRDVersion).where(PRDVersion.version_id == from_version))
    old_version = result.scalar_one()
    result = await db.execute(select(PRDVersion).where(PRDVersion.version_id == to_version))
    new_version = result.scalar_one()

    # Generate changelog
    diff_summary = new_version.diff_summary or {}
    changelog = await generate_changelog(old_version.prd_markdown, new_version.prd_markdown, diff_summary)

    return {
        "from_version": from_version,
        "to_version": to_version,
        "changelog": changelog,
        "diff_summary": diff_summary,
    }


__all__ = ["app"]
