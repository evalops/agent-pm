"""Plugin registry exports."""

from __future__ import annotations

from .registry import PluginRegistry

plugin_registry = PluginRegistry()

__all__ = ["plugin_registry", "PluginRegistry"]
