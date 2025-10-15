"""Plugin base classes for Agent PM."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from fastapi import APIRouter


@dataclass
class PluginMetadata:
    name: str
    description: str
    hooks: Sequence[str] = field(default_factory=tuple)
    config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


class PluginBase:
    """Base class for pluggable automation and feedback tools."""

    name: str = "plugin"
    description: str = ""
    hooks: Sequence[str] = ()

    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        self.config = config or {}

    def get_router(self) -> tuple[APIRouter, str] | None:  # pragma: no cover - default has no routes
        return None

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name=self.name,
            description=self.description,
            hooks=self.hooks,
            config=self.config,
            enabled=True,
        )
