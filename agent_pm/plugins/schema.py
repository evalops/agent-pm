"""Validation helpers for plugin configurations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, ConfigDict


class PluginConfigModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(..., min_length=1)
    module: str = Field(..., pattern=r"^[\w\.]+:[A-Za-z_][A-Za-z0-9_]*$")
    enabled: bool = True
    description: str | None = None
    hooks: list[str] | None = None
    config: dict[str, Any] | None = None


def load_plugin_config(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if isinstance(data, dict):
        data = [dict({"name": key}, **value) for key, value in data.items()]
    if not isinstance(data, list):
        raise ValueError("plugins.yaml must define a list of plugins")
    entries: list[dict[str, Any]] = []
    for entry in data:
        try:
            model = PluginConfigModel.model_validate(entry)
        except ValidationError as exc:
            raise ValueError(f"Invalid plugin config: {exc}") from exc
        entries.append(model.model_dump(exclude_unset=True))
    return entries


def dump_plugin_config(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalised: list[dict[str, Any]] = []
    for entry in entries:
        model = PluginConfigModel.model_validate(entry)
        normalised.append(model.model_dump(exclude_unset=True))
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(normalised, fh, sort_keys=False)
