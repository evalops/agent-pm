"""Long-term vector memory stub."""

import json
from pathlib import Path

import pandas as pd

from ..settings import settings


class VectorMemory:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.vector_store_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def load(self) -> dict[str, dict[str, str]]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return dict(data.items())

    def save(self, data: dict[str, dict[str, str]]) -> None:
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def record_prd(self, idea: str, prd_markdown: str) -> None:
        store = self.load()
        store[idea] = {"prd": prd_markdown}
        self.save(store)

    def to_dataframe(self) -> pd.DataFrame:
        data = self.load()
        rows: list[dict[str, str]] = []
        for idea, payload in data.items():
            rows.append({"idea": idea, **payload})
        return pd.DataFrame(rows)


vector_memory = VectorMemory()


__all__ = ["VectorMemory", "vector_memory"]
