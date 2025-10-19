from datetime import timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

import app as app_module
from agent_pm.api.auth import AdminKeyDep
from agent_pm.settings import settings
from app import app


async def _create_client():
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    await app_module.startup_event()

    async def cleanup():
        await client.aclose()
        await app_module.shutdown_event()

    client.cleanup = cleanup  # type: ignore[attr-defined]
    return client


@pytest.mark.asyncio
async def test_tasks_admin_endpoints_with_memory_backend(monkeypatch):
    monkeypatch.setattr(settings, "task_queue_backend", "memory")
    app.dependency_overrides[AdminKeyDep] = lambda: "admin-test-key"

    try:
        client = await _create_client()
        try:
            response = await client.get("/tasks/dead-letter")
            assert response.status_code == 200
            assert response.json()["dead_letter"] == []

            worker_resp = await client.get("/tasks/workers")
            assert worker_resp.status_code == 200
            assert worker_resp.json() == {"workers": {}}
        finally:
            await client.cleanup()
    finally:
        app.dependency_overrides.clear()


class StubQueue:
    def __init__(self):
        self.limit: int | None = None
        self.offset: int | None = None
        self.workflow_id: str | None = None
        self.error_type: str | None = None
        self.deleted: str | None = None
        self.requeued: str | None = None

    async def list_dead_letters(
        self,
        limit: int = 100,
        offset: int = 0,
        workflow_id: str | None = None,
        error_type: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        self.limit = limit
        self.offset = offset
        self.workflow_id = workflow_id
        self.error_type = error_type
        items = [
            {
                "task_id": "dead-1",
                "name": "explode",
                "retry_count": 3,
                "last_error": "boom",
                "metadata": {"workflow_id": "plan-123"},
                "error_type": "RuntimeError",
            }
        ]
        filtered = [item for item in items if not workflow_id or item["metadata"]["workflow_id"] == workflow_id]
        if error_type:
            filtered = [item for item in filtered if item.get("error_type") == error_type]
        return filtered, len(filtered)

    async def delete_dead_letter(self, task_id: str) -> None:
        self.deleted = task_id

    async def worker_heartbeats(self) -> dict[str, dict[str, str]]:
        return {"worker:1": {"status": "ok"}}

    async def get_task(self, task_id: str):  # pragma: no cover - minimal stub for routing
        return None

    async def requeue_dead_letter(self, task_id: str) -> dict[str, Any] | None:
        self.requeued = task_id
        return {"task_id": task_id}

    async def get_dead_letter(self, task_id: str) -> dict[str, Any] | None:
        if task_id == "dead-1":
            return {
                "task_id": "dead-1",
                "name": "explode",
                "retry_count": 3,
                "last_error": "boom",
            }
        return None

    async def purge_dead_letters(self) -> int:
        return 1

    async def purge_dead_letters_older_than(self, age):  # pragma: no cover - stubbed for API test
        return 0


@pytest.mark.asyncio
async def test_tasks_admin_endpoints_surface_queue_data(monkeypatch):
    monkeypatch.setattr(settings, "task_queue_backend", "memory")
    app.dependency_overrides[AdminKeyDep] = lambda: "admin-test-key"

    try:
        client = await _create_client()
        try:
            original_backend = settings.task_queue_backend
            original_queue = app_module._task_queue
            stub = StubQueue()
            app_module._task_queue = stub
            settings.task_queue_backend = "memory"
            try:
                dead_resp = await client.get(
                    "/tasks/dead-letter", params={"limit": 5, "offset": 0, "workflow_id": "plan-123"}
                )
                assert dead_resp.status_code == 200
                assert dead_resp.json()["dead_letter"][0]["task_id"] == "dead-1"
                assert stub.limit == 5
                assert stub.workflow_id == "plan-123"

                del_resp = await client.delete("/tasks/dead-letter/dead-1")
                assert del_resp.status_code == 200
                assert stub.deleted == "dead-1"

                detail_resp = await client.get("/tasks/dead-letter/dead-1")
                assert detail_resp.status_code == 200
                assert detail_resp.json()["task_id"] == "dead-1"

                worker_resp = await client.get("/tasks/workers")
                assert worker_resp.status_code == 200
                assert worker_resp.json() == {"workers": {"worker:1": {"status": "ok"}}}

                requeue_resp = await client.post("/tasks/dead-letter/dead-1/requeue")
                assert requeue_resp.status_code == 200
                assert stub.requeued == "dead-1"
                assert requeue_resp.json()["status"] == "requeued"

                purge_resp = await client.delete("/tasks/dead-letter")
                assert purge_resp.status_code == 200
                assert purge_resp.json()["deleted"] == 1

                purge_resp_age = await client.delete("/tasks/dead-letter", params={"older_than_minutes": 10})
                assert purge_resp_age.status_code == 200
            finally:
                settings.task_queue_backend = original_backend
                app_module._task_queue = original_queue
        finally:
            await client.cleanup()
    finally:
        app.dependency_overrides.clear()
