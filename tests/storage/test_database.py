import uuid

import pytest
from sqlalchemy import select

from agent_pm.storage import database


@pytest.fixture(scope="module")
def database_url(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("db") / "test.db"
    return f"sqlite+aiosqlite:///{db_path}"


@pytest.fixture(autouse=True)
def override_db_settings(monkeypatch, database_url):
    monkeypatch.setattr(database.settings, "database_url", database_url)
    monkeypatch.setattr(database.settings, "database_echo", False)
    yield
    # Reset cached engine/session factory between tests to avoid cross-suite bleed
    database._engine = None
    database._session_factory = None


@pytest.mark.asyncio
async def test_init_db_creates_tables():
    await database.init_db()

    engine = database.get_engine()
    async with engine.begin() as conn:

        def _inspect_tables(sync_conn):
            from sqlalchemy import inspect

            inspector = inspect(sync_conn)
            return inspector.get_table_names()

        tables = await conn.run_sync(_inspect_tables)
    expected_tables = {
        "tasks",
        "plans",
        "feedback",
        "prd_versions",
        "prd_approvals",
        "alignment_events",
    }
    assert expected_tables.issubset(set(tables))


@pytest.mark.asyncio
async def test_session_round_trip():
    await database.init_db()
    session_factory = database.get_session_factory()

    async with session_factory() as session:
        new_plan_id = uuid.uuid4().hex
        plan = database.Plan(
            plan_id=new_plan_id,
            title="Test",
            context="Context",
        )
        session.add(plan)
        await session.commit()

    async with session_factory() as session:
        stmt = select(database.Plan).where(database.Plan.plan_id == new_plan_id)
        result = await session.execute(stmt)
        fetched = result.scalar_one()
        assert fetched.title == "Test"
