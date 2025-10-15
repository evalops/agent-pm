"""Plugin registry and loader."""

from __future__ import annotations

import asyncio
import importlib
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from ..settings import settings
from .base import PluginBase, PluginMetadata

logger = logging.getLogger(__name__)


class PluginRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.plugin_config_path
        self._plugins: list[PluginBase] = []
        self._metadata: list[PluginMetadata] = []
        self._routers: list[tuple[Any, str]] = []
        self._load()

    def _load(self) -> None:
        config_entries = self._read_config()
        for entry in config_entries:
            if not entry.get("enabled", True):
                metadata = PluginMetadata(
                    name=entry.get("name", "unknown"),
                    description=entry.get("description", ""),
                    hooks=tuple(entry.get("hooks", [])),
                    config=entry.get("config", {}),
                    enabled=False,
                )
                self._metadata.append(metadata)
                continue

            try:
                plugin = self._instantiate(entry)
            except Exception as exc:  # pragma: no cover - defensive guard
                logger.exception("Failed to load plugin %s", entry.get("name"), exc_info=exc)
                continue

            self._plugins.append(plugin)
            meta = plugin.metadata()
            self._metadata.append(meta)
            router_info = plugin.get_router()
            if router_info:
                router, prefix = router_info
                self._routers.append((router, prefix))

    def _read_config(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or []
        if isinstance(data, dict):
            # Allow mapping form {name: {...}}
            data = [dict({"name": key}, **value) for key, value in data.items()]
        if not isinstance(data, list):
            raise ValueError("plugins.yaml must define a list of plugins")
        return data

    def _instantiate(self, entry: dict[str, Any]) -> PluginBase:
        module_ref = entry.get("module")
        if not module_ref:
            raise ValueError(f"Plugin entry {entry!r} missing 'module'")
        module_name, _, class_name = module_ref.partition(":")
        if not class_name:
            raise ValueError("Plugin module must be in 'module:ClassName' format")
        module = importlib.import_module(module_name)
        plugin_cls = getattr(module, class_name)
        if not issubclass(plugin_cls, PluginBase):
            raise TypeError(f"{class_name} is not a PluginBase subclass")
        plugin = plugin_cls(entry.get("config", {}))
        # Allow entry to override description/hooks metadata
        if entry.get("description"):
            plugin.description = entry["description"]
        if entry.get("hooks"):
            plugin.hooks = tuple(entry["hooks"])
        return plugin

    @property
    def plugins(self) -> list[PluginBase]:
        return list(self._plugins)

    @property
    def routers(self) -> list[tuple[Any, str]]:
        return list(self._routers)

    def list_metadata(self) -> list[dict[str, Any]]:
        return [meta.__dict__ for meta in self._metadata]

    def get(self, name: str) -> PluginBase | None:
        for plugin in self._plugins:
            if plugin.name == name:
                return plugin
        return None

    def fire(self, hook: str, *args, **kwargs) -> None:
        for plugin in self._plugins:
            handler = getattr(plugin, hook, None)
            if not callable(handler):
                continue
            try:
                result = handler(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    self._schedule(result)
            except Exception as exc:  # pragma: no cover - defensive guard
                logger.exception("Plugin %s hook %s failed", plugin.name, hook, exc_info=exc)

    def _schedule(self, coro: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coro)
        else:
            loop.create_task(coro)


def iter_plugins(registry: PluginRegistry) -> Iterable[PluginBase]:
    return registry.plugins
