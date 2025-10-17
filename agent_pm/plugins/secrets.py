"""Secret resolution utilities for plugins."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..settings import settings


def _load_secret_file(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    loader = yaml.safe_load if path.suffix.lower() in {".yaml", ".yml"} else json.loads
    try:
        data = loader(text) or {}
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


@lru_cache(maxsize=1)
def _secret_sources() -> dict[str, Any]:
    path = settings.plugin_secret_path
    if not path:
        return {}
    return _load_secret_file(path)


def refresh_secret_cache() -> None:
    """Invalidate cached secret data (used after secrets file updates)."""

    _secret_sources.cache_clear()  # type: ignore[attr-defined]


def _from_settings(key: str) -> Any:
    attr_name = key.lower()
    if not hasattr(settings, attr_name):
        return None
    return getattr(settings, attr_name)


def _from_secret_file(key: str, plugin_name: str | None) -> Any:
    data = _secret_sources()
    # 1. Plugin-specific overrides
    if plugin_name:
        per_plugin = data.get("plugins", {})
        if isinstance(per_plugin, dict):
            plugin_blob = per_plugin.get(plugin_name) or per_plugin.get(
                plugin_name.replace("-", "_")
            )
            if isinstance(plugin_blob, dict) and key in plugin_blob:
                return plugin_blob[key]
    # 2. Top-level key
    if key in data:
        return data[key]
    # 3. Global bucket
    global_blob = data.get("global", {})
    if isinstance(global_blob, dict):
        return global_blob.get(key)
    return None


def resolve_secret(
    key: str, *, plugin_name: str | None = None, overrides: dict[str, Any] | None = None
) -> Any:
    """Resolve a secret by checking overrides, environment, settings, then optional secret file."""

    if overrides:
        value = overrides.get(key)
        if isinstance(value, dict):
            if "value" in value:
                return value["value"]
            if "env" in value:
                env_key = value["env"]
                if isinstance(env_key, str):
                    env_val = os.getenv(env_key)
                    if env_val:
                        return env_val
        elif value not in (None, ""):
            return value

    env_value = os.getenv(key)
    if env_value:
        return env_value

    settings_value = _from_settings(key)
    if settings_value:
        return settings_value

    return _from_secret_file(key, plugin_name)


__all__ = ["resolve_secret", "refresh_secret_cache"]
