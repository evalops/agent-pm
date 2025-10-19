"""Periodic sync manager for external connectors."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Iterable

from agent_pm.connectors import (
    CalendarConnector,
    Connector,
    EmailConnector,
    GitHubConnector,
    GoogleDriveConnector,
    NotionConnector,
    SlackConnector,
)
from agent_pm.settings import settings
from agent_pm.storage.syncs import record_sync

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SyncJob:
    connector: Connector
    interval_seconds: int
    task: asyncio.Task[None] | None = field(default=None, init=False)
    last_success: datetime | None = field(default=None, init=False)


class PeriodicSyncManager:
    def __init__(self) -> None:
        self._jobs: list[SyncJob] = []
        self._running = False

    def register(self, connector: Connector, interval_seconds: int) -> None:
        if interval_seconds <= 0:
            interval_seconds = 60
        self._jobs.append(SyncJob(connector=connector, interval_seconds=interval_seconds))

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        for job in self._jobs:
            job.task = asyncio.create_task(self._run_job(job))
        logger.info("PeriodicSyncManager started with %d jobs", len(self._jobs))

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for job in self._jobs:
            if job.task is None:
                continue
            job.task.cancel()
        await asyncio.gather(*(job.task for job in self._jobs if job.task is not None), return_exceptions=True)
        for job in self._jobs:
            job.task = None
        logger.info("PeriodicSyncManager stopped")

    async def _run_job(self, job: SyncJob) -> None:
        connector = job.connector
        initial_delay = min(job.interval_seconds, 5)
        await asyncio.sleep(initial_delay)
        while self._running:
            if connector.enabled:
                since = job.last_success
                started = time.perf_counter()
                started_at = datetime.now(tz=UTC)
                try:
                    payloads = await connector.sync(since=since)
                except asyncio.CancelledError:  # pragma: no cover - cooperative cancellation
                    raise
                except Exception as exc:  # pragma: no cover - defensive logging
                    logger.exception("Connector sync failed: %s", connector.name, exc_info=exc)
                    duration_ms = (time.perf_counter() - started) * 1000
                    await self._record_sync(
                        connector.name,
                        "failed",
                        0,
                        duration_ms,
                        since,
                        started_at,
                        str(exc),
                    )
                else:
                    job.last_success = datetime.now(tz=UTC)
                    duration_ms = (time.perf_counter() - started) * 1000
                    await self._record_sync(
                        connector.name,
                        "success",
                        len(payloads),
                        duration_ms,
                        since,
                        started_at,
                        None,
                    )
                    logger.info(
                        "Connector sync completed",
                        extra={
                            "connector": connector.name,
                            "records": len(payloads),
                            "since": since.isoformat() if since else None,
                            "duration_ms": round(duration_ms, 3),
                        },
                    )
            else:
                logger.debug("Connector %s disabled; skipping sync", connector.name)
                await self._record_sync(connector.name, "skipped", 0, 0.0, job.last_success, datetime.now(tz=UTC), None)
            await asyncio.sleep(job.interval_seconds)

    async def _record_sync(
        self,
        connector: str,
        status: str,
        records: int,
        duration_ms: float,
        since: datetime | None,
        started_at: datetime,
        error: str | None,
    ) -> None:
        try:
            await record_sync(
                connector=connector,
                status=status,
                records=records,
                duration_ms=duration_ms,
                since=since,
                started_at=started_at,
                error=error,
                metadata={"started_at": started_at.isoformat()},
            )
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Failed to persist connector sync record", extra={"connector": connector, "status": status})


def create_default_sync_manager() -> PeriodicSyncManager:
    manager = PeriodicSyncManager()
    connectors: Iterable[tuple[Connector, int]] = (
        (GitHubConnector(), settings.github_sync_interval_seconds),
        (SlackConnector(), settings.slack_sync_interval_seconds),
        (EmailConnector(), settings.email_sync_interval_seconds),
        (CalendarConnector(), settings.calendar_sync_interval_seconds),
        (GoogleDriveConnector(), settings.google_drive_sync_interval_seconds),
        (NotionConnector(), settings.notion_sync_interval_seconds),
    )
    for connector, interval in connectors:
        manager.register(connector, interval)
    return manager
