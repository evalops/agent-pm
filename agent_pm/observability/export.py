"""Async trace export to external observability systems."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from agent_pm.settings import settings

from .metrics import record_client_call

logger = logging.getLogger(__name__)


async def export_trace_webhook(trace_data: dict[str, Any], webhook_url: str) -> None:
    """Export trace to webhook endpoint."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            with record_client_call("webhook"):
                response = await client.post(
                    webhook_url,
                    json=trace_data,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
            logger.info("Trace exported to webhook: %s", webhook_url)
    except Exception as exc:
        logger.error("Failed to export trace to webhook: %s", exc)


async def export_trace_s3(trace_data: dict[str, Any], bucket: str, key: str) -> None:
    """Export trace to S3 (requires boto3/aioboto3)."""
    try:
        # Lazy import to avoid hard dependency
        import aioboto3

        session = aioboto3.Session()
        async with session.client("s3") as s3:
            with record_client_call("s3"):
                await s3.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=json.dumps(trace_data, indent=2).encode(),
                    ContentType="application/json",
                )
            logger.info("Trace exported to S3: s3://%s/%s", bucket, key)
    except ImportError:
        logger.warning("aioboto3 not installed; skipping S3 export")
    except Exception as exc:
        logger.error("Failed to export trace to S3: %s", exc)


async def export_trace(trace_path: Path) -> None:
    """Export trace to configured backends (webhook, S3)."""
    if not trace_path.exists():
        logger.warning("Trace file not found: %s", trace_path)
        return

    with record_client_call("trace_read"):
        trace_data = json.loads(trace_path.read_text())

    tasks = []

    # Export to webhook if configured
    webhook_url = getattr(settings, "trace_export_webhook", None)
    if webhook_url:
        tasks.append(export_trace_webhook(trace_data, webhook_url))

    # Export to S3 if configured
    s3_bucket = getattr(settings, "trace_export_s3_bucket", None)
    s3_prefix = getattr(settings, "trace_export_s3_prefix", "traces/")
    if s3_bucket:
        key = f"{s3_prefix}{trace_path.name}"
        tasks.append(export_trace_s3(trace_data, s3_bucket, key))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    else:
        logger.debug("No trace export backends configured")


def schedule_trace_export(trace_path: Path) -> None:
    """Schedule background export of trace (fire-and-forget)."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(export_trace(trace_path))
    except RuntimeError:
        # No event loop running; skip async export
        logger.debug("No event loop for trace export; skipping")
