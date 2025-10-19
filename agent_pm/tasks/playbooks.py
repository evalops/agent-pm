"""Remediation playbooks for automated task recovery."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

import httpx

from agent_pm.clients import pagerduty_client, slack_client
from agent_pm.settings import settings

logger = logging.getLogger(__name__)

PlaybookHandler = Callable[[dict[str, Any], Any, str], Awaitable[None]]


async def _notify_slack(payload: dict[str, Any], queue: Any, error_type: str) -> None:
    channel = settings.task_queue_alert_channel or slack_client.channel
    if not channel:
        return
    message = (
        ":rotating_light: Remediation triggered\n"
        f"Queue: `{getattr(queue, 'queue_name', 'unknown')}`\n"
        f"Error: `{error_type}`\n"
        f"Task: `{payload.get('name', 'unknown')}`"
    )
    if settings.dry_run or not slack_client.enabled:
        logger.warning("Remediation Slack notification skipped (dry run)")
        return
    await slack_client.post_digest(message, channel)


async def _invoke_webhook(payload: dict[str, Any], queue: Any, error_type: str) -> None:
    url = settings.task_queue_alert_webhook_url
    if not url:
        return
    body = {
        "queue": getattr(queue, "queue_name", "unknown"),
        "error_type": error_type,
        "task": payload.get("name", "unknown"),
        "task_id": payload.get("task_id"),
        "metadata": payload.get("metadata", {}),
        "playbook": "webhook",
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=body, timeout=10)
        response.raise_for_status()


async def _log_only(payload: dict[str, Any], queue: Any, error_type: str) -> None:
    logger.warning(
        "Remediation playbook invoked", extra={"queue": getattr(queue, "queue_name", "unknown"), "error": error_type, "task": payload.get("task_id")}
    )


async def _notify_pagerduty(payload: dict[str, Any], queue: Any, error_type: str) -> None:
    if not pagerduty_client.enabled:
        return
    summary = f"Auto-remediation triggered for {error_type}"
    details = {
        "queue": getattr(queue, "queue_name", "unknown"),
        "task_id": payload.get("task_id"),
        "task_name": payload.get("name"),
        "metadata": payload.get("metadata", {}),
    }
    await pagerduty_client.trigger_incident(summary, severity="error", **details)


PLAYBOOKS: dict[str, PlaybookHandler] = {
    "log_only": _log_only,
    "notify_slack": _notify_slack,
    "webhook": _invoke_webhook,
    "notify_pagerduty": _notify_pagerduty,
}


async def run_playbook(name: str, payload: dict[str, Any], queue: Any, error_type: str) -> None:
    handler = PLAYBOOKS.get(name)
    if not handler:
        logger.warning("Unknown playbook requested: %s", name)
        return
    try:
        await handler(payload, queue, error_type)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Playbook %s failed: %s", name, exc)
