"""In-memory broadcast for realtime alignment updates."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

Subscriber = tuple[asyncio.Queue, asyncio.AbstractEventLoop]

_subscribers: set[Subscriber] = set()


def register_subscriber() -> asyncio.Queue:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers.add((queue, loop))
    return queue


def unregister_subscriber(queue: asyncio.Queue) -> None:
    to_remove = [pair for pair in _subscribers if pair[0] is queue]
    for pair in to_remove:
        _subscribers.discard(pair)


def broadcast_alignment_event(event: dict[str, Any]) -> None:
    if not _subscribers:
        return

    for queue, loop in list(_subscribers):

        def _put(evt=event, q=queue) -> None:
            with suppress(
                asyncio.QueueFull
            ):  # pragma: no cover - queues are unbounded by default
                q.put_nowait(evt)

        try:
            loop.call_soon_threadsafe(_put)
        except RuntimeError:  # pragma: no cover - loop may be closed during shutdown
            _subscribers.discard((queue, loop))


__all__ = ["register_subscriber", "unregister_subscriber", "broadcast_alignment_event"]
