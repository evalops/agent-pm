import pytest

import app as app_module


@pytest.mark.asyncio
async def test_lifespan_starts_and_stops_procedure_scheduler(monkeypatch):
    class StubQueue:
        def __init__(self) -> None:
            self.started = False
            self.stopped = False

        async def start(self) -> None:
            self.started = True

        async def stop(self) -> None:
            self.stopped = True

    class StubManager:
        def __init__(self) -> None:
            self.started = False
            self.stopped = False

        async def start(self) -> None:
            self.started = True

        async def stop(self) -> None:
            self.stopped = True

    class StubScheduler:
        def __init__(self) -> None:
            self.started = False
            self.stopped = False

        async def start(self) -> None:
            self.started = True

        async def stop(self) -> None:
            self.stopped = True

    queue = StubQueue()
    manager = StubManager()
    scheduler = StubScheduler()

    async def fake_get_task_queue():
        return queue

    monkeypatch.setattr(app_module, "get_task_queue", fake_get_task_queue)
    monkeypatch.setattr(app_module, "create_default_sync_manager", lambda: manager)
    monkeypatch.setattr(app_module, "scheduler", scheduler)

    async with app_module.lifespan(app_module.app):
        assert queue.started is True
        assert manager.started is True
        assert scheduler.started is True

    assert scheduler.stopped is True
    assert manager.stopped is True
    assert queue.stopped is True
