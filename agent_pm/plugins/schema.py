"""Validation helpers for plugin configurations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class PluginConfigModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(..., min_length=1)
    module: str = Field(..., pattern=r"^[\w\.]+:[A-Za-z_][A-Za-z0-9_]*$")
    enabled: bool = True
    description: str | None = None
    hooks: list[str] | None = None
    config: dict[str, Any] | None = None


def load_plugin_config(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not path.exists():
        return [], []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if isinstance(data, dict):
        data = [dict({"name": key}, **value) for key, value in data.items()]
    if not isinstance(data, list):
        return [], [{"index": 0, "entry": data, "error": "plugins.yaml must define a list"}]
    entries: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, entry in enumerate(data):
        try:
            model = PluginConfigModel.model_validate(entry)
        except ValidationError as exc:
            errors.append({"index": index, "entry": entry, "error": str(exc)})
            continue
        payload = model.model_dump(exclude_unset=True)
        payload["__index__"] = index
        entries.append(payload)
    return entries, errors


def dump_plugin_config(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(entries, fh, sort_keys=False)
