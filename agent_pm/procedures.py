"""Procedure loader for deterministic workflows."""

from pathlib import Path
from typing import Any

import yaml

from .settings import settings


class ProcedureLoader:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or settings.procedure_dir

    def load(self) -> dict[str, Any]:
        procedures: dict[str, Any] = {}
        if not self.directory.exists():
            return procedures
        for path in self.directory.glob("*.yaml"):
            content = yaml.safe_load(path.read_text(encoding="utf-8"))
            procedures[path.stem] = content
        return procedures


loader = ProcedureLoader()


__all__ = ["ProcedureLoader", "loader"]
