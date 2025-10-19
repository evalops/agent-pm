"""Persistence helpers for connector synchronization history."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select

from agent_pm.storage.database import ConnectorSync, get_session_factory
from agent_pm.utils.datetime import utc_now


async def record_sync(
    *,
    connector: str,
    status: str,
    records: int,
    duration_ms: float | None,
    since: datetime | None,
    started_at: datetime | None,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        entry = ConnectorSync(
            connector=connector,
            status=status,
            records=records,
            duration_ms=round(duration_ms or 0.0, 3) if duration_ms is not None else None,
            details={
                "since": since.isoformat() if since else None,
                **(metadata or {}),
            },
            error=error,
            completed_at=utc_now(),
            started_at=started_at or utc_now(),
        )
        session.add(entry)
        await session.commit()


async def list_recent_syncs(limit: int = 50) -> list[dict[str, Any]]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        stmt = (
            select(ConnectorSync)
            .order_by(desc(ConnectorSync.started_at))
            .limit(max(limit, 1))
        )
        try:
            result = await session.execute(stmt)
        except Exception:
            return []
        rows: Sequence[ConnectorSync] = result.scalars().all()
        return [
            {
                "connector": row.connector,
                "status": row.status,
                "records": row.records,
                "duration_ms": row.duration_ms,
                "metadata": row.details or {},
                "error": row.error,
                "started_at": row.started_at.astimezone(UTC).isoformat() if row.started_at else None,
                "completed_at": row.completed_at.astimezone(UTC).isoformat() if row.completed_at else None,
            }
            for row in rows
        ]
