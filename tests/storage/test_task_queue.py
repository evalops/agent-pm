import asyncio

import pytest

from agent_pm.storage.tasks import TaskQueue, TaskStatus


async def sample_task(value: int, *, delay: float = 0.0) -> int:
    if delay:
        await asyncio.sleep(delay)
    return value * 2


async def failing_task(counter: dict[str, int]) -> None:
    counter.setdefault("attempts", 0)
    counter["attempts"] += 1
    raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_task_queue_executes_successfully():
    queue = TaskQueue(max_workers=1)
    await queue.start()

    task_id = await queue.enqueue("double", sample_task, 3)

    # Wait for completion
    for _ in range(20):
        task = await queue.get_task(task_id)
        if task and task.status == TaskStatus.COMPLETED:
            break
        await asyncio.sleep(0.05)

    await queue.stop()

    task = await queue.get_task(task_id)
    assert task is not None
    assert task.status == TaskStatus.COMPLETED
    assert task.result == 6
    assert task.retry_count == 0


@pytest.mark.asyncio
async def test_task_queue_retries_then_fails(monkeypatch):
    queue = TaskQueue(max_workers=1)
    await queue.start()

    counter: dict[str, int] = {}

    task_id = await queue.enqueue("fails", failing_task, counter, max_retries=2)

    # Wait for retries to exhaust
    for _ in range(60):
        task = await queue.get_task(task_id)
        if task and task.status in {TaskStatus.FAILED, TaskStatus.COMPLETED}:
            break
        await asyncio.sleep(0.1)

    await queue.stop()

    task = await queue.get_task(task_id)
    assert task is not None
    assert task.status == TaskStatus.FAILED
    assert counter["attempts"] == 2
    assert task.retry_count == 2
    assert task.error == "boom"
