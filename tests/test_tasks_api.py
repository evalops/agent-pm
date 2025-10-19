import pytest
from httpx import ASGITransport, AsyncClient

import app as app_module
from agent_pm.api.auth import AdminKeyDep
from agent_pm.settings import settings
from app import app


class AppClient:
    def __init__(self):
        self.transport = ASGITransport(app=app)
        self.client = AsyncClient(transport=self.transport, base_url="http://test")

    async def __aenter__(self):
        await app_module.startup_event()
        return self.client

    async def __aexit__(self, exc_type, exc, tb):
        await self.client.aclose()
        await app_module.shutdown_event()


async def _create_client():
    return AppClient()


@pytest.mark.asyncio
async def test_tasks_admin_endpoints_with_memory_backend(monkeypatch):
    monkeypatch.setattr(settings, "task_queue_backend", "memory")
    app.dependency_overrides[AdminKeyDep] = lambda: "admin-test-key"

    try:
        async with await _create_client() as client:
            response = await client.get("/tasks/dead-letter")
            assert response.status_code == 200
            assert response.json() == {"dead_letter": [], "total": 0}

            worker_resp = await client.get("/tasks/workers")
            assert worker_resp.status_code == 200
            assert worker_resp.json() == {"workers": {}}
    finally:
        app.dependency_overrides.clear()


class StubQueue:
    def __init__(self):
        self.limit: int | None = None
        self.deleted: str | None = None

    async def list_dead_letters(self, limit: int = 100):
        self.limit = limit
        return [
            {
                "task_id": "dead-1",
                "name": "explode",
                "retry_count": 3,
                "last_error": "boom",
            }
        ]

    async def delete_dead_letter(self, task_id: str) -> None:
        self.deleted = task_id

    async def worker_heartbeats(self) -> dict[str, dict[str, str]]:
        return {"worker:1": {"status": "ok"}}

    async def get_task(self, task_id: str):  # pragma: no cover - minimal stub for routing
        return None


@pytest.mark.asyncio
async def test_tasks_admin_endpoints_surface_queue_data(monkeypatch):
    monkeypatch.setattr(settings, "task_queue_backend", "memory")
    app.dependency_overrides[AdminKeyDep] = lambda: "admin-test-key"

    try:
        async with await _create_client() as client:
            original_backend = settings.task_queue_backend
            original_queue = app_module._task_queue
            stub = StubQueue()
            app_module._task_queue = stub
            settings.task_queue_backend = "memory"
            try:
                dead_resp = await client.get("/tasks/dead-letter", params={"limit": 5})
                assert dead_resp.status_code == 200
                assert dead_resp.json()["dead_letter"][0]["task_id"] == "dead-1"
                assert stub.limit == 5

                del_resp = await client.delete("/tasks/dead-letter/dead-1")
                assert del_resp.status_code == 200
                assert stub.deleted == "dead-1"

                worker_resp = await client.get("/tasks/workers")
                assert worker_resp.status_code == 200
                assert worker_resp.json() == {"workers": {"worker:1": {"status": "ok"}}}
            finally:
                settings.task_queue_backend = original_backend
                app_module._task_queue = original_queue
    finally:
        app.dependency_overrides.clear()
