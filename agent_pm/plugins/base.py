"""Plugin base classes for Agent PM."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, Sequence

from fastapi import APIRouter, HTTPException

from ..settings import settings

if TYPE_CHECKING:  # pragma: no cover - type hints only
    from .registry import PluginRegistry


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
    required_secrets: Sequence[str] = ()

    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        self.config = config or {}
        self.registry: "PluginRegistry | None" = None
        self.active: bool = True

    def get_router(self) -> tuple[APIRouter, str] | None:  # pragma: no cover - default has no routes
        return None

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name=self.name,
            description=self.description,
            hooks=self.hooks,
            config=self.config,
            enabled=self.is_enabled,
        )

    def emit(self, hook: str, **payload: Any) -> None:
        if self.registry is None:
            return
        self.registry.fire(hook, source=self, **payload)

    @property
    def is_enabled(self) -> bool:
        if self.registry is not None:
            return self.registry.is_enabled(self.name)
        return self.active

    def ensure_enabled(self) -> None:
        if not self.is_enabled:
            raise HTTPException(status_code=503, detail=f"Plugin {self.name} is disabled")

    def missing_secrets(self) -> list[str]:
        missing: list[str] = []
        for key in self.required_secrets:
            if os.getenv(key):
                continue
            attr_name = key.lower()
            value = getattr(settings, attr_name, None)
            if value:
                continue
            missing.append(key)
        return missing

    def on_enable(self) -> None:  # pragma: no cover - overridable hook
        return None

    def on_disable(self) -> None:  # pragma: no cover - overridable hook
        return None

    def on_reload(self) -> None:  # pragma: no cover - overridable hook
        return None
