"""FastAPI service exposing Agent PM planner and ticket flows."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from agent_pm.agent_sdk import planner_tools_default_enabled, reload_agent_profiles
from agent_pm.alignment.log import (
    fetch_alignment_events,
    get_alignment_summary,
    record_alignment_followup_event,
    summarize_alignment_events,
)
from agent_pm.alignment.stream import register_subscriber, unregister_subscriber
from agent_pm.api.auth import AdminKeyDep, APIKeyDep
from agent_pm.api.guardrails import guardrail_context, rate_limited
from agent_pm.api.health import check_all_dependencies
from agent_pm.api.rate_limit import (
    enforce_concurrency_limit,
    enforce_rate_limit,
    release_concurrency,
)
from agent_pm.clients import calendar_client, github_client, jira_client, slack_client
from agent_pm.memory import TraceMemory
from agent_pm.models import (
    BatchIdea,
    Idea,
    JiraIssuePayload,
    ReviewEvent,
    SlackDigest,
    TicketPlan,
)
from agent_pm.observability.export import schedule_trace_export
from agent_pm.observability.logging import configure_logging
from agent_pm.observability.metrics import (
    latest_metrics,
    record_alignment_export,
)
from agent_pm.observability.structured import (
    configure_structured_logging,
    get_correlation_id,
    set_correlation_id,
)
from agent_pm.observability.traces import list_traces as list_trace_files
from agent_pm.observability.traces import persist_trace, summarize_trace
from agent_pm.planner import generate_plan
from agent_pm.plugins import plugin_registry
from agent_pm.prd.changelog import generate_changelog
from agent_pm.prd.versions import (
    approve_version,
    create_branch,
    create_version,
    get_blame,
    get_version_history,
)
from agent_pm.procedures import loader as procedure_loader
from agent_pm.settings import settings
from agent_pm.storage.database import PRDVersion, get_db
from agent_pm.storage.tasks import TaskStatus, get_task_queue
from agent_pm.tools import registry

@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _task_queue
    _task_queue = await get_task_queue()
    await _task_queue.start()
    logger.info("Agent PM service started")
    try:
        yield
    finally:
        if _task_queue:
            await _task_queue.stop()
        logger.info("Agent PM service stopped")


lifespan_app = FastAPI(title="Agent PM", version="0.1.0", lifespan=lifespan)

if settings.log_format == "json":
    configure_structured_logging()
else:
    configure_logging(settings.trace_dir)

logger = logging.getLogger(__name__)
_jira_lock = asyncio.Lock()
_task_queue = None

plugin_registry.attach_app(lifespan_app)


class FollowupUpdate(BaseModel):
    status: str


app = lifespan_app


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


@app.post(
    "/plan",
    dependencies=[Depends(enforce_rate_limit), Depends(enforce_concurrency_limit)],
)
async def plan(idea: Idea, _api_key: APIKeyDep = None) -> dict[str, Any]:
    set_correlation_id(str(uuid.uuid4()))
    logger.info(
        "Plan request received",
        extra={"title": idea.title, "correlation_id": get_correlation_id()},
    )
    try:
        return await _plan_impl(idea)
    finally:
        release_concurrency()


@app.get("/alignments", dependencies=[Depends(enforce_rate_limit)])
async def list_alignments(limit: int = 50, _admin_key: AdminKeyDep = None) -> dict[str, Any]:
    events = await fetch_alignment_events(limit)
    summary = summarize_alignment_events(events)
    return {"events": events, "summary": summary}


@app.websocket("/alignments/ws")
async def alignments_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = register_subscriber()
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        unregister_subscriber(queue)


@app.post("/alignments/{event_id}/followup", dependencies=[Depends(enforce_rate_limit)])
async def alignment_followup(event_id: str, payload: FollowupUpdate, _admin_key: AdminKeyDep = None) -> dict[str, Any]:
    updated = await record_alignment_followup_event(event_id, payload.status)
    if not updated:
        raise HTTPException(status_code=404, detail="Alignment event not found")
    return {"event_id": event_id, "status": payload.status}


@app.get("/alignments/summary", dependencies=[Depends(enforce_rate_limit)])
async def alignment_summary(limit: int = 100, _admin_key: AdminKeyDep = None) -> dict[str, Any]:
    events, summary = get_alignment_summary(limit)
    record_alignment_export("summary")
    return {"events": events, "summary": summary}


@app.get("/plugins", dependencies=[Depends(enforce_rate_limit)])
async def list_plugins_endpoint(_admin_key: AdminKeyDep = None) -> dict[str, Any]:
    return {"plugins": plugin_registry.list_metadata()}


@app.get("/plugins/discover", dependencies=[Depends(enforce_rate_limit)])
async def discover_plugins_endpoint(_admin_key: AdminKeyDep = None) -> dict[str, Any]:
    return {"entry_points": plugin_registry.discover_plugins()}


@app.post("/plugins/{name}/enable", dependencies=[Depends(enforce_rate_limit)])
async def enable_plugin(name: str, _admin_key: AdminKeyDep = None) -> dict[str, Any]:
    try:
        metadata = plugin_registry.set_enabled(name, True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"plugin": metadata}


@app.post("/plugins/{name}/disable", dependencies=[Depends(enforce_rate_limit)])
async def disable_plugin(name: str, _admin_key: AdminKeyDep = None) -> dict[str, Any]:
    try:
        metadata = plugin_registry.set_enabled(name, False)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"plugin": metadata}


@app.post("/plugins/reload", dependencies=[Depends(enforce_rate_limit)])
async def reload_plugins(_admin_key: AdminKeyDep = None) -> dict[str, Any]:
    plugin_registry.reload()
    return {"plugins": plugin_registry.list_metadata()}


@app.post("/plugins/{name}/reload", dependencies=[Depends(enforce_rate_limit)])
async def reload_plugin(name: str, _admin_key: AdminKeyDep = None) -> dict[str, Any]:
    try:
        metadata = plugin_registry.reload_plugin(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"plugin": metadata}


class PluginConfigUpdate(BaseModel):
    config: dict[str, Any]


class PluginInstallRequest(BaseModel):
    module: str | None = None
    entry_point: str | None = None
    name: str | None = None
    enabled: bool = False
    config: dict[str, Any] | None = None
    description: str | None = None

    @model_validator(mode="after")
    def _validate_source(self) -> PluginInstallRequest:
        if not self.module and not self.entry_point:
            raise ValueError("module or entry_point is required")
        return self


@app.post("/plugins/{name}/config", dependencies=[Depends(enforce_rate_limit)])
async def update_plugin_config(name: str, update: PluginConfigUpdate, _admin_key: AdminKeyDep = None) -> dict[str, Any]:
    try:
        metadata = plugin_registry.update_config(name, update.config)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"plugin": metadata}


@app.post("/plugins/install", dependencies=[Depends(enforce_rate_limit)])
async def install_plugin_endpoint(request: PluginInstallRequest, _admin_key: AdminKeyDep = None) -> dict[str, Any]:
    module_ref = request.module
    plugin_name = request.name
    description = request.description
    hooks = None

    if request.entry_point and not module_ref:
        catalogue = {item["entry_point"]: item for item in plugin_registry.discover_plugins()}
        info = catalogue.get(request.entry_point)
        if info is None:
            raise HTTPException(status_code=404, detail=f"Entry point {request.entry_point} not found")
        module_ref = info["module"]
        if plugin_name is None:
            plugin_name = info.get("plugin_name")
        if description is None and info.get("description"):
            description = info.get("description")
        hooks = info.get("hooks")

    if not module_ref:
        raise HTTPException(status_code=400, detail="Unable to determine module reference for plugin")

    try:
        metadata = plugin_registry.install_plugin(
            module_ref,
            name=plugin_name,
            enabled=request.enabled,
            config=request.config or {},
            description=description,
            hooks=hooks,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"plugin": metadata}


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


@app.get("/tasks")
async def list_tasks(status: str | None = None, limit: int = 50, _admin_key: AdminKeyDep = None) -> dict[str, Any]:
    """List all tasks with optional status filter."""
    task_queue = await get_task_queue()
    task_status = TaskStatus(status) if status else None
    tasks = await task_queue.list_tasks(status=task_status, limit=limit)
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


@app.get("/tasks/dead-letter")
async def list_dead_letter(
    limit: int = 50,
    offset: int = 0,
    workflow_id: str | None = None,
    error_type: str | None = None,
    _admin_key: AdminKeyDep = None,
) -> dict[str, Any]:
    task_queue = await get_task_queue()
    items, total = await task_queue.list_dead_letters(
        limit=limit, offset=offset, workflow_id=workflow_id, error_type=error_type
    )
    config = {
        "auto_requeue_errors": settings.task_queue_auto_requeue_errors,
        "alert_threshold": settings.task_queue_alert_threshold,
        "alert_window_minutes": settings.task_queue_alert_window_minutes,
        "alert_cooldown_minutes": settings.task_queue_alert_cooldown_minutes,
        "alert_channel": settings.task_queue_alert_channel,
    }
    return {
        "dead_letter": [
            {
                **item,
                "metadata": item.get("metadata", {}),
                "error_type": item.get("error_type"),
                "error_message": item.get("error_message"),
                "stack_trace": item.get("stack_trace"),
            }
            for item in items
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
        "auto_triage": config,
    }


@app.get("/tasks/dead-letter/{task_id}")
async def get_dead_letter(task_id: str, _admin_key: AdminKeyDep = None) -> dict[str, Any]:
    task_queue = await get_task_queue()
    item = await task_queue.get_dead_letter(task_id)
    if not item:
        raise HTTPException(status_code=404, detail="Dead-letter task not found")
    return item


@app.delete("/tasks/dead-letter/{task_id}")
async def delete_dead_letter(task_id: str, _admin_key: AdminKeyDep = None) -> dict[str, Any]:
    task_queue = await get_task_queue()
    await task_queue.delete_dead_letter(task_id)
    return {"task_id": task_id, "status": "deleted"}


@app.delete("/tasks/dead-letter")
async def purge_dead_letters(
    older_than_minutes: int | None = None, _admin_key: AdminKeyDep = None
) -> dict[str, int]:
    task_queue = await get_task_queue()
    if older_than_minutes is None:
        deleted = await task_queue.purge_dead_letters()
    else:
        deleted = await task_queue.purge_dead_letters_older_than(timedelta(minutes=older_than_minutes))
    return {"deleted": deleted}


@app.get("/tasks/workers")
async def worker_status(_admin_key: AdminKeyDep = None) -> dict[str, Any]:
    task_queue = await get_task_queue()
    return {"workers": await task_queue.worker_heartbeats()}


@app.get("/tasks/{task_id}")
async def get_task(task_id: str, _admin_key: AdminKeyDep = None) -> dict[str, Any]:
    """Get task status by ID."""
    task_queue = await get_task_queue()
    task = await task_queue.get_task(task_id)
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


@app.post("/tasks/dead-letter/{task_id}/requeue")
async def requeue_dead_letter(task_id: str, _admin_key: AdminKeyDep = None) -> dict[str, Any]:
    task_queue = await get_task_queue()
    payload = await task_queue.requeue_dead_letter(task_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Dead-letter task not found")
    return {"task_id": payload.get("task_id", task_id), "status": "requeued"}


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
