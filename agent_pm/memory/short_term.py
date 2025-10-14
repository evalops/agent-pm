"""Short-term conversational trace memory."""

from collections import deque


class TraceMemory:
    def __init__(self, maxlen: int = 50) -> None:
        self._events: deque[dict[str, str]] = deque(maxlen=maxlen)

    def add(self, role: str, content: str) -> None:
        self._events.append({"role": role, "content": content})

    def dump(self) -> list[dict[str, str]]:
        return list(self._events)


__all__ = ["TraceMemory"]
