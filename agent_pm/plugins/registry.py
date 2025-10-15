"""Plugin registry and loader."""

from __future__ import annotations

import asyncio
import importlib
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from ..metrics import record_plugin_hook_failure, record_plugin_hook_invocation
from ..settings import settings
from .base import PluginBase, PluginMetadata
from .schema import dump_plugin_config, load_plugin_config, PluginConfigModel

logger = logging.getLogger(__name__)


class PluginRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.plugin_config_path
        self._entries: list[dict[str, Any]] = []
        self._plugins: dict[str, PluginBase] = {}
        self._metadata: dict[str, PluginMetadata] = {}
        self._routers: dict[str, tuple[Any, str]] = {}
        self._mounted_plugins: set[str] = set()
        self._hook_stats: dict[str, dict[str, dict[str, int]]] = {}
        self._app: FastAPI | None = None
        self._load()

    # ------------------------------------------------------------------
    # Loading & persistence
    # ------------------------------------------------------------------
    def _load(self) -> None:
        entries = self._read_config()
        self._entries = entries
        seen: set[str] = set()

        for entry in entries:
            name = entry.get("name")
            if not name:
                logger.warning("Skipping plugin entry without name: %s", entry)
                continue
            seen.add(name)
            enabled = bool(entry.get("enabled", True))
            config = entry.get("config", {}) or {}
            description = entry.get("description", "")
            hooks = tuple(entry.get("hooks", []))

            plugin = self._plugins.get(name)
            if plugin is None:
                try:
                    plugin = self._instantiate(entry, config)
                except Exception as exc:  # pragma: no cover - defensive guard
                    logger.exception("Failed to instantiate plugin %s", name, exc_info=exc)
                    continue
                self._plugins[name] = plugin
            else:
                plugin.config = config

            plugin.description = description or plugin.description
            if hooks:
                plugin.hooks = hooks
            plugin.active = enabled
            plugin.registry = self

            metadata = PluginMetadata(
                name=name,
                description=plugin.description,
                hooks=plugin.hooks,
                config=config,
                enabled=enabled,
            )
            self._metadata[name] = metadata

            if name not in self._routers:
                router_info = plugin.get_router()
                if router_info:
                    self._routers[name] = router_info
                    self._mount_router(name, router_info)

        # Remove plugins that were deleted from config
        for name in list(self._plugins.keys() - seen):
            self._plugins.pop(name, None)
            self._metadata.pop(name, None)
            self._routers.pop(name, None)
            self._hook_stats.pop(name, None)

    def _read_config(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return load_plugin_config(self.path)

    def _write_config(self) -> None:
        dump_plugin_config(self.path, self._entries)

    def _instantiate(self, entry: dict[str, Any], config: dict[str, Any]) -> PluginBase:
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
        plugin = plugin_cls(config)
        if entry.get("description"):
            plugin.description = entry["description"]
        if entry.get("hooks"):
            plugin.hooks = tuple(entry["hooks"])
        plugin.registry = self
        plugin.active = bool(entry.get("enabled", True))
        return plugin

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def attach_app(self, app: FastAPI) -> None:
        self._app = app
        self._mount_existing_routers()

    def reload(self) -> None:
        previous_active = {name for name, plugin in self._plugins.items() if plugin.active}
        self._load()
        for name, plugin in self._plugins.items():
            if name in previous_active and plugin.active and name in self._routers:
                # Router already mounted previously; nothing to do
                continue
            if plugin.active and name in self._routers:
                self._mount_router(name, self._routers[name])

    def set_enabled(self, name: str, enabled: bool) -> dict[str, Any]:
        entry = next((item for item in self._entries if item.get("name") == name), None)
        if entry is None:
            raise KeyError(f"Plugin {name} not found")
        entry["enabled"] = bool(enabled)
        self._write_config()
        self._load()
        return self.metadata_for(name)

    def update_config(self, name: str, config: dict[str, Any]) -> dict[str, Any]:
        entry = next((item for item in self._entries if item.get("name") == name), None)
        if entry is None:
            raise KeyError(f"Plugin {name} not found")
        candidate = PluginConfigModel(
            name=name,
            module=entry["module"],
            enabled=entry.get("enabled", True),
            description=entry.get("description"),
            hooks=entry.get("hooks"),
            config=config,
        )
        entry["config"] = candidate.config or {}
        self._write_config()
        self._load()
        return self.metadata_for(name)

    def metadata_for(self, name: str) -> dict[str, Any]:
        meta = self._metadata.get(name)
        if meta:
            data = meta.__dict__.copy()
        else:
            entry = next((item for item in self._entries if item.get("name") == name), None)
            if entry is None:
                raise KeyError(f"Plugin {name} not registered")
            data = {
                "name": name,
                "description": entry.get("description", ""),
                "hooks": tuple(entry.get("hooks", [])),
                "config": entry.get("config", {}),
                "enabled": bool(entry.get("enabled", False)),
            }
        data["active"] = self.is_enabled(name)
        data["hook_stats"] = self._hook_stats.get(name, {})
        return data

    @property
    def plugins(self) -> list[PluginBase]:
        return list(self._plugins.values())

    @property
    def routers(self) -> list[tuple[Any, str]]:
        return list(self._routers.values())

    def list_metadata(self) -> list[dict[str, Any]]:
        metadata: list[dict[str, Any]] = []
        for entry in self._entries:
            name = entry.get("name")
            if not name:
                continue
            try:
                metadata.append(self.metadata_for(name))
            except KeyError:
                continue
        return metadata

    def get(self, name: str) -> PluginBase | None:
        return self._plugins.get(name)

    def is_enabled(self, name: str) -> bool:
        plugin = self._plugins.get(name)
        return bool(plugin and plugin.active)

    def fire(self, hook: str, *args, source: PluginBase | None = None, **kwargs) -> None:
        for plugin in self._plugins.values():
            if source is not None and plugin is source:
                continue
            if not plugin.active:
                continue
            handler = getattr(plugin, hook, None)
            if not callable(handler):
                continue
            stats = self._hook_stats.setdefault(plugin.name, {})
            hook_stats = stats.setdefault(hook, {"invocations": 0, "failures": 0})
            record_plugin_hook_invocation(plugin.name, hook)
            try:
                result = handler(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    self._schedule(result)
                hook_stats["invocations"] += 1
            except Exception as exc:  # pragma: no cover - defensive guard
                record_plugin_hook_failure(plugin.name, hook)
                hook_stats["invocations"] += 1
                hook_stats["failures"] += 1
                logger.exception("Plugin %s hook %s failed", plugin.name, hook, exc_info=exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _schedule(self, coro: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coro)
        else:
            loop.create_task(coro)

    def _mount_existing_routers(self) -> None:
        for name, router_info in self._routers.items():
            self._mount_router(name, router_info)

    def _mount_router(self, name: str, router_info: tuple[Any, str]) -> None:
        if name in self._mounted_plugins:
            return
        if self._app is None:
            return
        router, prefix = router_info
        self._app.include_router(router, prefix=prefix)
        self._mounted_plugins.add(name)


def iter_plugins(registry: PluginRegistry) -> Iterable[PluginBase]:
    return registry.plugins
