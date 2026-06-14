"""Cron-style scheduler for procedure execution."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime

import yaml

from agent_pm.settings import settings

logger = logging.getLogger(__name__)

Schedule = dict[str, str]  # e.g. {"cron": "0 9 * * 1", "description": "..."}


class ProcedureScheduler:
    """Loads a schedule definition and runs procedures on cron expressions.

    A simple in-process scheduler — no external dependency. Reads
    ``config/procedure_schedule.yaml`` which maps procedure names to
    cron expressions (5-field, minute granularity).

    Example schedule YAML:
        weekly_progress_review:
          cron: "0 9 * * 1"   # Monday 9am
        dependabot_triage:
          cron: "0 10 * * *"  # Daily 10am
    """

    def __init__(self, schedule_path: str | None = None) -> None:
        self._schedule_path = schedule_path or str(settings.procedure_dir.parent / "config" / "procedure_schedule.yaml")
        self._schedules: dict[str, str] = {}
        self._last_runs: dict[str, datetime] = {}
        self._task: asyncio.Task[None] | None = None
        self._running = False

    def load(self) -> dict[str, str]:
        path = self._schedule_path
        try:
            with open(path) as f:
                raw = yaml.safe_load(f) or {}
        except FileNotFoundError:
            logger.info("No procedure schedule found at %s — nothing scheduled", path)
            return {}
        self._schedules = {name: entry["cron"] for name, entry in raw.items()}
        return self._schedules

    def _cron_matches(self, cron: str, dt: datetime) -> bool:
        """Check if a 5-field cron expression matches *right now* (minute granularity)."""
        parts = cron.strip().split()
        if len(parts) != 5:
            return False

        minute, hour, dom, month, dow = parts

        def _match(field: str, value: int) -> bool:
            if field == "*":
                return True
            for alt in field.split(","):
                alt = alt.strip()
                if "/" in alt:
                    base, step_s = alt.split("/", 1)
                    step = int(step_s)
                    if step <= 0:
                        continue
                    if base == "*":
                        if value % step == 0:
                            return True
                        continue
                    if "-" in base:
                        lo_s, hi_s = base.split("-", 1)
                        lo = int(lo_s)
                        hi = int(hi_s)
                        if lo <= value <= hi and (value - lo) % step == 0:
                            return True
                        continue
                if "-" in alt:
                    lo_s, hi_s = alt.split("-", 1)
                    if int(lo_s) <= value <= int(hi_s):
                        return True
                elif alt == str(value):
                    return True
            return False

        # Python weekday: 0=Mon, 6=Sun. Cron weekday: 0=Sun, 1=Mon, ..., 6=Sat.
        cron_dow_map: dict[int, int] = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 0}
        python_dow = dt.weekday()  # 0=Monday
        cron_dow_value = cron_dow_map.get(python_dow, python_dow)

        return (
            _match(minute, dt.minute)
            and _match(hour, dt.hour)
            and _match(dom, dt.day)
            and _match(month, dt.month)
            and _match(dow, cron_dow_value)
        )

    async def _run_procedure(self, name: str) -> None:
        """Execute a named procedure."""
        from agent_pm.procedure_runner import execute_procedure
        from agent_pm.procedures import loader

        procedures = loader.load()
        if name not in procedures:
            logger.warning("Scheduled procedure '%s' not found in procedures/", name)
            return

        logger.info("Running scheduled procedure: %s", name)

        try:
            result = await execute_procedure(name)
            logger.info("Procedure '%s' completed (plan_id=%s)", name, result.get("plan_id"))
        except Exception:
            logger.exception("Procedure '%s' failed", name)

    async def _tick(self) -> None:
        """Single scheduler tick — check all scheduled procedures."""
        now = datetime.now(tz=UTC)
        for name, cron_expr in self._schedules.items():
            if not self._cron_matches(cron_expr, now):
                continue
            last = self._last_runs.get(name)
            # Only run once per minute
            if last and (now - last).total_seconds() < 120:
                continue
            self._last_runs[name] = now
            await self._run_procedure(name)

    async def _loop(self) -> None:
        """Main scheduler loop — checks every 60 seconds."""
        while self._running:
            try:
                await self._tick()
            except Exception:
                logger.exception("Scheduler tick error")
            await asyncio.sleep(60)

    async def start(self) -> None:
        self.load()
        if not self._schedules:
            logger.info("No scheduled procedures — scheduler idle")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Procedure scheduler started (%d procedures)", len(self._schedules))

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None


scheduler = ProcedureScheduler()

__all__ = ["ProcedureScheduler", "scheduler"]
