from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

import app as app_module
from agent_pm.api.auth import AdminKeyDep
from agent_pm.settings import settings
from agent_pm.storage import syncs as sync_storage
from app import app


async def _create_client():
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    await app.router.startup()

    async def cleanup():
        await app.router.shutdown()
        await client.aclose()

    client.cleanup = cleanup  # type: ignore[attr-defined]
    return client


@pytest.mark.asyncio
async def test_tasks_admin_endpoints_with_memory_backend(monkeypatch):
    monkeypatch.setattr(settings, "task_queue_backend", "memory")
    app.dependency_overrides[AdminKeyDep] = lambda: "admin-test-key"

    try:
        client = await _create_client()
        response = await client.get("/tasks/dead-letter")
        assert response.status_code == 200
        assert response.json()["dead_letter"] == []

        worker_resp = await client.get("/tasks/workers")
        assert worker_resp.status_code == 200
        assert worker_resp.json() == {"workers": {}}

        health_resp = await client.get("/tasks/health")
        assert health_resp.status_code == 200
        assert "queue" in health_resp.json()
        await client.cleanup()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_sync_status_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "task_queue_backend", "memory")
    app.dependency_overrides[AdminKeyDep] = lambda: "admin-test-key"

    records = [
        {
            "connector": "github",
            "status": "success",
            "records": 3,
            "duration_ms": 42.0,
            "metadata": {"since": None},
            "error": None,
            "started_at": "2024-01-01T00:00:00+00:00",
            "completed_at": "2024-01-01T00:00:01+00:00",
        }
    ]

    async def fake_list_recent_syncs(limit: int = 50):
        assert limit == 25
        return records

    monkeypatch.setattr(sync_storage, "list_recent_syncs", fake_list_recent_syncs)

    try:
        client = await _create_client()
        response = await client.get("/sync/status", params={"limit": 25})
        assert response.status_code == 200
        payload = response.json()
        assert payload["syncs"] == records
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
        original_backend = settings.task_queue_backend
        stub = StubQueue()
        original_get_queue = app_module.get_task_queue
        original_queue = app_module._task_queue

        async def fake_get_queue():
            return stub

        monkeypatch.setattr(app_module, "get_task_queue", fake_get_queue)
        settings.task_queue_backend = "memory"
        try:
            dead_resp = await client.get(
                "/tasks/dead-letter", params={"limit": 5, "offset": 0, "workflow_id": "plan-123"}
            )
            assert dead_resp.status_code == 200
            payload = dead_resp.json()
            assert payload["dead_letter"][0]["task_id"] == "dead-1"
            assert payload["auto_triage"]["auto_requeue_errors"] == settings.task_queue_auto_requeue_errors
            assert payload["auto_triage"]["alert_threshold"] == settings.task_queue_alert_threshold
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
            monkeypatch.setattr(app_module, "get_task_queue", original_get_queue)
            await client.cleanup()
    finally:
        app.dependency_overrides.clear()
