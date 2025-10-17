"""Plugin registry and loader."""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
import time
from collections import deque
from collections.abc import Iterable
from importlib import metadata
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from ..metrics import record_plugin_hook_failure, record_plugin_hook_invocation
from ..settings import settings
from ..utils.datetime import utc_now_isoformat
from .base import PluginBase, PluginMetadata
from .schema import PluginConfigModel, dump_plugin_config, load_plugin_config

logger = logging.getLogger(__name__)


class PluginRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.plugin_config_path
        self._entries: list[dict[str, Any]] = []
        self._plugins: dict[str, PluginBase] = {}
        self._metadata: dict[str, PluginMetadata] = {}
        self._routers: dict[str, tuple[Any, str]] = {}
        self._mounted_plugins: set[str] = set()
        self._hook_stats: dict[str, dict[str, dict[str, Any]]] = {}
        self._hook_history: dict[str, dict[str, deque[dict[str, Any]]]] = {}
        self._config_errors: list[dict[str, Any]] = []
        self._errors: dict[str, list[str]] = {}
        self._order: list[str] = []
        self._positions: dict[str, int] = {}
        self._invalid_entries: dict[str, dict[str, Any]] = {}
        self._app: FastAPI | None = None
        self._load()

        for plugin in self._plugins.values():
            if not plugin.active:
                continue
            try:
                plugin.on_enable()
            except Exception as exc:  # pragma: no cover - defensive guard
                self._append_error(plugin.name, str(exc))
                logger.exception(
                    "Plugin %s on_enable failed during bootstrap",
                    plugin.name,
                    exc_info=exc,
                )

    # ------------------------------------------------------------------
    # Loading & persistence
    # ------------------------------------------------------------------
    def _load(self) -> None:
        raw_entries, config_errors = self._read_config()
        self._positions = {}
        clean_entries: list[dict[str, Any]] = []
        for item in raw_entries:
            entry = dict(item)
            index = entry.pop("__index__", len(self._positions))
            name = entry.get("name")
            if name is not None:
                self._positions[name] = index
            clean_entries.append(entry)

        self._entries = clean_entries
        self._config_errors = config_errors
        self._invalid_entries = {}
        self._errors = {}
        self._metadata = {}
        self._order = []
        old_stats = self._hook_stats
        old_history = self._hook_history
        new_plugins: dict[str, PluginBase] = {}
        new_routers: dict[str, tuple[Any, str]] = {}

        for entry in self._entries:
            name = entry.get("name")
            if not name:
                logger.warning("Skipping plugin entry without name: %s", entry)
                continue
            self._errors.setdefault(name, [])

            enabled = bool(entry.get("enabled", True))
            config = entry.get("config", {}) or {}
            description = entry.get("description", "")
            hooks = tuple(entry.get("hooks", []))

            try:
                plugin = self._instantiate(entry, config)
            except Exception as exc:  # pragma: no cover - defensive guard
                logger.exception("Failed to instantiate plugin %s", name, exc_info=exc)
                self._errors[name].append(str(exc))
                metadata = PluginMetadata(
                    name=name,
                    description=description,
                    hooks=hooks,
                    config=config,
                    enabled=enabled,
                )
                self._metadata[name] = metadata
                continue

            plugin.description = description or plugin.description
            if hooks:
                plugin.hooks = hooks
            plugin.active = enabled
            plugin.registry = self
            new_plugins[name] = plugin

            metadata = PluginMetadata(
                name=name,
                description=plugin.description,
                hooks=plugin.hooks,
                config=plugin.config,
                enabled=enabled,
            )
            self._metadata[name] = metadata

            router_info = plugin.get_router()
            if router_info:
                new_routers[name] = router_info

        self._plugins = new_plugins
        self._routers = new_routers
        self._hook_stats = {name: old_stats.get(name, {}) for name in self._plugins}
        self._hook_history = {name: old_history.get(name, {}) for name in self._plugins}

        for idx, error in enumerate(self._config_errors):
            entry = error.get("entry", {}) or {}
            name = entry.get("name") or f"invalid_plugin_{idx}"
            index = error.get("index", len(self._positions) + idx)
            self._positions.setdefault(name, index)
            self._invalid_entries[name] = entry
            self._errors.setdefault(name, []).append(
                error.get("error", "invalid configuration")
            )
            if name not in self._metadata:
                metadata = PluginMetadata(
                    name=name,
                    description=entry.get("description", ""),
                    hooks=tuple(entry.get("hooks", [])),
                    config=entry.get("config", {}),
                    enabled=False,
                )
                self._metadata[name] = metadata

        self._order = [
            name
            for name, _ in sorted(self._positions.items(), key=lambda item: item[1])
        ]

    def _read_config(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not self.path.exists():
            return [], []
        return load_plugin_config(self.path)

    def _write_config(self) -> None:
        entry_map = {
            entry.get("name"): dict(entry)
            for entry in self._entries
            if entry.get("name")
        }
        combined: list[tuple[int, dict[str, Any]]] = []

        for name, entry in entry_map.items():
            index = self._positions.get(name, len(combined))
            combined.append((index, dict(entry)))

        for name, entry in self._invalid_entries.items():
            index = self._positions.get(name, len(combined))
            combined.append((index, dict(entry)))

        combined.sort(key=lambda item: item[0])
        dump_plugin_config(self.path, [item[1] for item in combined])

    def _load_plugin_class(self, module_ref: str) -> type[PluginBase]:
        module_name, _, class_name = module_ref.partition(":")
        if not module_name or not class_name:
            raise ValueError("Plugin module must be in 'module:ClassName' format")
        module = importlib.import_module(module_name)
        plugin_cls = getattr(module, class_name)
        if not issubclass(plugin_cls, PluginBase):
            raise TypeError(f"{class_name} is not a PluginBase subclass")
        return plugin_cls

    def _instantiate(self, entry: dict[str, Any], config: dict[str, Any]) -> PluginBase:
        module_ref = entry.get("module")
        if not module_ref:
            raise ValueError(f"Plugin entry {entry!r} missing 'module'")
        plugin_cls = self._load_plugin_class(module_ref)
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
        previous_plugins = self._plugins.copy()
        previously_enabled = {
            name: plugin.active for name, plugin in previous_plugins.items()
        }
        self._load()

        for name, plugin in self._plugins.items():
            prev_active = previously_enabled.get(name, False)
            if plugin.active and name in self._routers:
                self._mount_router(name, self._routers[name])
            if name in previous_plugins:
                try:
                    plugin.on_reload()
                except Exception as exc:  # pragma: no cover - best effort
                    logger.exception("Plugin %s on_reload failed", name, exc_info=exc)
            if plugin.active and not prev_active:
                try:
                    plugin.on_enable()
                except Exception as exc:  # pragma: no cover - best effort
                    logger.exception("Plugin %s on_enable failed", name, exc_info=exc)

        for name, plugin in previous_plugins.items():
            new_meta = self._metadata.get(name)
            new_enabled = bool(new_meta.enabled) if new_meta else False
            if previously_enabled.get(name) and not new_enabled:
                try:
                    plugin.on_disable()
                except Exception as exc:  # pragma: no cover - best effort
                    logger.exception("Plugin %s on_disable failed", name, exc_info=exc)

    def set_enabled(self, name: str, enabled: bool) -> dict[str, Any]:
        entry = next((item for item in self._entries if item.get("name") == name), None)
        if entry is None:
            raise KeyError(f"Plugin {name} not found")
        entry["enabled"] = bool(enabled)
        self._write_config()
        self.reload()
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
        self.reload()
        return self.metadata_for(name)

    def metadata_for(self, name: str) -> dict[str, Any]:
        meta = self._metadata.get(name)
        if meta:
            data = meta.__dict__.copy()
        else:
            entry = next(
                (item for item in self._entries if item.get("name") == name), None
            )
            if entry is None:
                raise KeyError(f"Plugin {name} not registered")
            data = {
                "name": name,
                "description": entry.get("description", ""),
                "hooks": tuple(entry.get("hooks", [])),
                "config": entry.get("config", {}),
                "enabled": bool(entry.get("enabled", False)),
            }
        plugin = self._plugins.get(name)
        config_snapshot = plugin.config if plugin else data.get("config", {})
        data["config"] = self._sanitize_config(config_snapshot)
        data["active"] = bool(plugin and plugin.active)
        data["hook_stats"] = self._format_hook_stats(name)
        history = self._hook_history.get(name, {})
        data["hook_history"] = {
            hook: list(entries) for hook, entries in history.items()
        }
        data["errors"] = list(self._errors.get(name, []))
        data["invalid"] = name in self._invalid_entries or name not in self._plugins
        if plugin:
            data["hooks"] = tuple(plugin.hooks)
            data["enabled"] = plugin.active
            data["secrets"] = {
                "required": list(plugin.required_secrets),
                "missing": plugin.missing_secrets(),
            }
        else:
            data.setdefault("secrets", {"required": [], "missing": []})
        return data

    @property
    def plugins(self) -> list[PluginBase]:
        return list(self._plugins.values())

    @property
    def routers(self) -> list[tuple[Any, str]]:
        return list(self._routers.values())

    def list_metadata(self) -> list[dict[str, Any]]:
        metadata: list[dict[str, Any]] = []
        for name in self._order:
            try:
                metadata.append(self.metadata_for(name))
            except KeyError:
                continue
        return metadata

    def discover_plugins(self) -> list[dict[str, Any]]:
        try:
            eps = metadata.entry_points()
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.warning("Failed to load entry points: %s", exc)
            return []

        if hasattr(eps, "select"):
            group = eps.select(group="agent_pm.plugins")  # type: ignore[attr-defined]
        else:  # pragma: no cover - legacy compatibility
            group = [ep for ep in eps if getattr(ep, "group", "") == "agent_pm.plugins"]

        configured_modules = {entry.get("module") for entry in self._entries}
        discovered: list[dict[str, Any]] = []
        for ep in group:
            info: dict[str, Any] = {
                "entry_point": ep.name,
                "module": ep.value,
                "configured": ep.value in configured_modules,
            }
            dist = getattr(ep, "dist", None)
            if dist is not None:
                info["distribution"] = getattr(dist, "name", None)
            try:
                loaded = ep.load()
            except Exception as exc:  # pragma: no cover - defensive guard
                info["error"] = str(exc)
                discovered.append(info)
                continue

            plugin_cls: type[PluginBase] | None = None
            if isinstance(loaded, type) and issubclass(loaded, PluginBase):
                plugin_cls = loaded
            elif isinstance(loaded, PluginBase):
                plugin_cls = loaded.__class__
            if plugin_cls is not None:
                try:
                    instance = (
                        plugin_cls({}) if not isinstance(loaded, PluginBase) else loaded
                    )
                except Exception as exc:  # pragma: no cover - defensive guard
                    info["error"] = str(exc)
                else:
                    info["plugin_name"] = getattr(instance, "name", ep.name)
                    info["description"] = getattr(instance, "description", "")
                    info["hooks"] = list(getattr(instance, "hooks", []))
            if "plugin_name" not in info:
                info["plugin_name"] = ep.name
                info["description"] = info.get("description")
                info["hooks"] = info.get("hooks", [])
            discovered.append(info)

        return sorted(discovered, key=lambda item: item["entry_point"])

    def install_plugin(
        self,
        module_ref: str,
        *,
        name: str | None = None,
        enabled: bool = False,
        config: dict[str, Any] | None = None,
        description: str | None = None,
        hooks: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        plugin_cls = self._load_plugin_class(module_ref)
        try:
            instance = plugin_cls(config or {})
        except Exception as exc:  # pragma: no cover - defensive guard
            raise ValueError(
                f"Failed to instantiate plugin {module_ref}: {exc}"
            ) from exc

        plugin_name = (
            name or getattr(instance, "name", None) or plugin_cls.__name__.lower()
        )
        if any(entry.get("name") == plugin_name for entry in self._entries):
            raise ValueError(f"Plugin {plugin_name} already configured")

        entry = {
            "name": plugin_name,
            "module": module_ref,
            "enabled": bool(enabled),
            "description": (
                description
                if description is not None
                else getattr(instance, "description", "")
            ),
            "hooks": (
                list(hooks)
                if hooks is not None
                else list(getattr(instance, "hooks", []))
            ),
            "config": config or {},
        }
        self._entries.append(entry)
        self._positions[plugin_name] = len(self._positions)
        self._write_config()
        self.reload()
        return self.metadata_for(plugin_name)

    def reload_plugin(self, name: str) -> dict[str, Any]:
        entry = next((item for item in self._entries if item.get("name") == name), None)
        if entry is None:
            raise KeyError(f"Plugin {name} not found")
        module_ref = entry.get("module")
        if not module_ref:
            raise ValueError(f"Plugin {name} missing module reference")
        module_name, _, _ = module_ref.partition(":")
        importlib.invalidate_caches()
        if module_name in sys.modules:
            importlib.reload(sys.modules[module_name])
        else:
            importlib.import_module(module_name)
        self.reload()
        return self.metadata_for(name)

    def get(self, name: str) -> PluginBase | None:
        return self._plugins.get(name)

    def is_enabled(self, name: str) -> bool:
        plugin = self._plugins.get(name)
        return bool(plugin and plugin.active)

    def fire(
        self, hook: str, *args, source: PluginBase | None = None, **kwargs
    ) -> None:
        for plugin in self._plugins.values():
            if source is not None and plugin is source:
                continue
            if not plugin.active:
                continue
            handler = getattr(plugin, hook, None)
            if not callable(handler):
                continue
            hook_stats = self._ensure_hook_stats(plugin.name, hook)
            hook_stats["invocations"] += 1
            record_plugin_hook_invocation(plugin.name, hook)
            start = time.perf_counter()
            try:
                result = handler(*args, **kwargs)
            except Exception as exc:  # pragma: no cover - defensive guard
                duration_ms = (time.perf_counter() - start) * 1000
                self._handle_hook_failure(plugin, hook, hook_stats, duration_ms, exc)
                continue

            if asyncio.iscoroutine(result):

                async def _runner(
                    coro=result,
                    started=start,
                    stats=hook_stats,
                    plugin_ref=plugin,
                ) -> None:
                    try:
                        await coro
                    except Exception as async_exc:  # pragma: no cover - defensive guard
                        duration_ms = (time.perf_counter() - started) * 1000
                        self._handle_hook_failure(
                            plugin_ref, hook, stats, duration_ms, async_exc
                        )
                    else:
                        duration_ms = (time.perf_counter() - started) * 1000
                        self._handle_hook_success(plugin_ref, hook, stats, duration_ms)

                self._schedule(_runner())
            else:
                duration_ms = (time.perf_counter() - start) * 1000
                self._handle_hook_success(plugin, hook, hook_stats, duration_ms)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _ensure_hook_stats(self, plugin_name: str, hook: str) -> dict[str, Any]:
        stats = self._hook_stats.setdefault(plugin_name, {})
        return stats.setdefault(
            hook,
            {
                "invocations": 0,
                "failures": 0,
                "total_duration_ms": 0.0,
                "last_duration_ms": 0.0,
            },
        )

    def _record_hook_history(
        self,
        plugin_name: str,
        hook: str,
        status: str,
        duration_ms: float,
        error: Exception | None = None,
    ) -> None:
        history = self._hook_history.setdefault(plugin_name, {})
        bucket = history.setdefault(hook, deque(maxlen=100))
        entry: dict[str, Any] = {
            "timestamp": utc_now_isoformat(),
            "status": status,
            "duration_ms": round(duration_ms, 3),
        }
        if error is not None:
            entry["error"] = str(error)
        bucket.append(entry)

    def _append_error(self, plugin_name: str, message: str) -> None:
        errors = self._errors.setdefault(plugin_name, [])
        errors.append(message)
        if len(errors) > 10:
            self._errors[plugin_name] = errors[-10:]

    def _sanitize_config(self, config: dict[str, Any] | None) -> dict[str, Any]:
        if not config:
            return {}
        sanitized: dict[str, Any] = {}
        for key, value in config.items():
            if key.lower() == "secrets" and isinstance(value, dict):
                sanitized[key] = {
                    secret_key: ("***" if secret_val not in (None, "") else None)
                    for secret_key, secret_val in value.items()
                }
            else:
                sanitized[key] = value
        return sanitized

    def _format_hook_stats(self, plugin_name: str) -> dict[str, Any]:
        formatted: dict[str, Any] = {}
        stats = self._hook_stats.get(plugin_name, {})
        for hook, values in stats.items():
            invocations = max(values.get("invocations", 0), 0)
            total_duration = values.get("total_duration_ms", 0.0)
            item = {
                "invocations": invocations,
                "failures": values.get("failures", 0),
                "total_duration_ms": round(total_duration, 3),
                "last_duration_ms": round(values.get("last_duration_ms", 0.0), 3),
            }
            item["avg_duration_ms"] = (
                round(total_duration / invocations, 3) if invocations else 0.0
            )
            formatted[hook] = item
        return formatted

    def _handle_hook_success(
        self,
        plugin: PluginBase,
        hook: str,
        hook_stats: dict[str, Any],
        duration_ms: float,
    ) -> None:
        hook_stats["last_duration_ms"] = round(duration_ms, 3)
        hook_stats["total_duration_ms"] = (
            hook_stats.get("total_duration_ms", 0.0) + duration_ms
        )
        self._record_hook_history(plugin.name, hook, "success", duration_ms)

    def _handle_hook_failure(
        self,
        plugin: PluginBase,
        hook: str,
        hook_stats: dict[str, Any],
        duration_ms: float,
        exc: Exception,
    ) -> None:
        hook_stats["failures"] = hook_stats.get("failures", 0) + 1
        hook_stats["last_duration_ms"] = round(duration_ms, 3)
        hook_stats["total_duration_ms"] = (
            hook_stats.get("total_duration_ms", 0.0) + duration_ms
        )
        self._append_error(plugin.name, str(exc))
        record_plugin_hook_failure(plugin.name, hook)
        self._record_hook_history(plugin.name, hook, "error", duration_ms, error=exc)
        logger.exception("Plugin %s hook %s failed", plugin.name, hook, exc_info=exc)

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
