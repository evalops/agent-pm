"""Simple warehouse export plugin that appends hook telemetry to a JSONL file."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import PluginBase


class WarehouseExportPlugin(PluginBase):
    name = "warehouse_export"
    description = "Persist hook events to a local JSONL file for warehouse ingestion"
    hooks = ("post_ticket_export", "post_alignment_event", "on_feedback")

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._prepare_path()

    def _prepare_path(self) -> None:
        path = self.config.get("path", "./data/plugin_events.jsonl")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def on_enable(self) -> None:
        self._prepare_path()

    def on_reload(self) -> None:
        self._prepare_path()

    def _write_record(self, record: dict[str, Any]) -> None:
        record.setdefault("timestamp", datetime.utcnow().isoformat())
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

    def post_ticket_export(
        self,
        kind: str,
        destination: str,
        rows: int,
        statuses: list[str] | None = None,
        **_: Any,
    ) -> None:
        if not self.is_enabled:
            return
        self._write_record(
            {
                "event": "ticket_export",
                "kind": kind,
                "destination": destination,
                "rows": rows,
                "statuses": statuses,
            }
        )

    def post_alignment_event(self, event: dict[str, Any]) -> None:
        if not self.is_enabled:
            return
        self._write_record({"event": "alignment_event", "payload": event})

    def on_feedback(self, feedback: dict[str, Any]) -> None:
        if not self.is_enabled:
            return
        self._write_record({"event": "feedback", "payload": feedback})
