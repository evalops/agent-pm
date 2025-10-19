"""Base connector protocol."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any


class Connector(ABC):
    """Abstract connector interface used by the sync manager."""

    name: str

    def __init__(self, *, name: str) -> None:
        self.name = name

    @property
    @abstractmethod
    def enabled(self) -> bool:
        """Return True if the connector has the credentials it needs."""

    @abstractmethod
    async def sync(self, *, since: datetime | None = None) -> list[dict[str, Any]]:
        """Fetch data updated since the provided timestamp."""

    def format_metadata(self, **kwargs: Any) -> dict[str, Any]:
        data = {"connector": self.name}
        data.update(kwargs)
        return data
