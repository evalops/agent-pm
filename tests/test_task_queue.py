"""Tests for async task queue."""

import asyncio

import pytest

from agent_pm.storage.tasks import TaskQueue, TaskStatus


async def sample_task(value: int) -> int:
    await asyncio.sleep(0.01)
    return value * 2


async def failing_task() -> None:
    raise ValueError("Task failed intentionally")


@pytest.mark.asyncio
async def test_task_queue_enqueue_and_execute():
    queue = TaskQueue(max_workers=2)
    await queue.start()
    try:
        task_id = await queue.enqueue("sample_task", sample_task, 5)
        assert task_id

        # Wait for task to complete
        await asyncio.sleep(0.5)
        task = await queue.get_task(task_id)
        assert task
        assert task.status == TaskStatus.COMPLETED
        assert task.result == 10
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_task_queue_retry_logic():
    queue = TaskQueue(max_workers=1)
    await queue.start()
    try:
        task_id = await queue.enqueue("failing_task", failing_task, max_retries=2)
        assert task_id

        # Wait for task to fail and retry
        await asyncio.sleep(3)
        task = await queue.get_task(task_id)
        assert task
        assert task.status == TaskStatus.FAILED
        assert task.retry_count == 2
        assert "Task failed intentionally" in task.error
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_task_queue_list_tasks():
    queue = TaskQueue(max_workers=2)
    await queue.start()
    try:
        task_id1 = await queue.enqueue("task1", sample_task, 1)
        task_id2 = await queue.enqueue("task2", sample_task, 2)

        tasks = await queue.list_tasks()
        assert len(tasks) >= 2
        task_ids = {t.task_id for t in tasks}
        assert task_id1 in task_ids
        assert task_id2 in task_ids
    finally:
        await queue.stop()
