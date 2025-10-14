"""Tool registry loader."""

from pathlib import Path
from typing import Any

import yaml

from .settings import settings


class ToolRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.tool_config_path
        self._tools = self._load()

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or []
        if not isinstance(data, list):
            raise ValueError("tools.yaml must define a list of tools")
        return data

    @property
    def tools(self) -> list[dict[str, Any]]:
        return self._tools

    def as_openai_tools(self) -> list[dict[str, Any]]:
        openai_tools: list[dict[str, Any]] = []
        for tool in self._tools:
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("summary", ""),
                        "parameters": tool.get("params", {}),
                    },
                }
            )
        return openai_tools


registry = ToolRegistry()


__all__ = ["registry", "ToolRegistry"]
