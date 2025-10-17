"""Feedback collection plugin providing submission API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import AdminKeyDep, APIKeyDep
from ..metrics import record_feedback_submission
from ..rate_limit import enforce_rate_limit
from ..settings import settings
from ..utils.datetime import utc_now_isoformat
from .base import PluginBase


class FeedbackEntry(BaseModel):
    title: str = Field(..., description="Plan title or identifier")
    rating: int | None = Field(None, ge=1, le=5)
    comment: str | None = None
    submitted_by: str | None = None
    source: str | None = None


class FeedbackPlugin(PluginBase):
    name = "feedback_collector"
    description = "Collect plan feedback via API"
    hooks = ()

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        storage_path = self.config.get("storage_path", "./data/feedback.json")
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.storage_path.exists():
            self.storage_path.write_text("[]", encoding="utf-8")
        self.router = APIRouter(prefix=self.config.get("route_prefix", "/plugins/feedback"), tags=["feedback"])
        self._register_routes()
        self.recent_feedback: list[dict[str, Any]] = []

    def get_router(self):
        return self.router, ""

    def _register_routes(self) -> None:
        @self.router.post("", dependencies=[Depends(enforce_rate_limit)])
        async def submit(entry: FeedbackEntry, _api_key: APIKeyDep = None) -> dict[str, Any]:
            self.ensure_enabled()
            saved = self._append(entry)
            record_feedback_submission(entry.source or "unknown")
            self.recent_feedback.append(saved)
            self.emit("on_feedback", feedback=saved)
            return saved

        @self.router.get("", dependencies=[Depends(enforce_rate_limit)])
        async def list_feedback(_admin_key: AdminKeyDep = None) -> dict[str, Any]:
            self.ensure_enabled()
            return {"feedback": self._load()}

        @self.router.delete("", dependencies=[Depends(enforce_rate_limit)])
        async def clear_feedback(_admin_key: AdminKeyDep = None) -> dict[str, Any]:
            self.ensure_enabled()
            if not settings.dry_run:
                raise HTTPException(status_code=403, detail="Clearing feedback requires DRY_RUN=true")
            self.storage_path.write_text("[]", encoding="utf-8")
            return {"cleared": True}

    def _load(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.storage_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:  # pragma: no cover - defensive
            return []

    def _append(self, entry: FeedbackEntry) -> dict[str, Any]:
        records = self._load()
        payload = entry.model_dump()
        payload["submitted_at"] = utc_now_isoformat()
        records.append(payload)
        self.storage_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        return payload
