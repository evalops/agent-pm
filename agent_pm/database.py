"""Database models and session management for Agent PM."""

from __future__ import annotations

import enum
from collections.abc import AsyncGenerator
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, Float, Integer, String, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from agent_pm.settings import settings


class Base(DeclarativeBase):
    """Base class for all database models."""

    pass


class TaskStatusDB(str, enum.Enum):
    """Task status enum for database."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


class Task(Base):
    """Persisted background task."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[TaskStatusDB] = mapped_column(Enum(TaskStatusDB), default=TaskStatusDB.PENDING, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=True)  # serialized args/kwargs
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Plan(Base):
    """Persisted planning request and result."""

    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(512))
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    constraints: Mapped[list] = mapped_column(JSON, default=list)
    prd_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    ticket_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status_digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    critic_review: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    revision_history: Mapped[list] = mapped_column(JSON, default=list)
    trace_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)  # OpenAI embedding vector
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Feedback(Base):
    """Human feedback on generated PRDs."""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(64), index=True)
    user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5 stars
    edited_prd: Mapped[str | None] = mapped_column(Text, nullable=True)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class PRDVersion(Base):
    """Git-like version control for PRDs."""

    __tablename__ = "prd_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # SHA-like hash
    plan_id: Mapped[str] = mapped_column(String(64), index=True)  # Links to Plan
    parent_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    branch: Mapped[str] = mapped_column(String(255), default="main", index=True)
    prd_markdown: Mapped[str] = mapped_column(Text)
    commit_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="draft", index=True)  # draft, approved, merged
    diff_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # lines added/removed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class PRDApproval(Base):
    """PR-style approval workflow for PRD versions."""

    __tablename__ = "prd_approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[str] = mapped_column(String(64), index=True)
    reviewer: Mapped[str] = mapped_column(String(255))
    reviewer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(64))  # approved, rejected, changes_requested
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class AlignmentEvent(Base):
    """Stored goal alignment event for analytics/reporting."""

    __tablename__ = "alignment_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(512))
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggestions: Mapped[list] = mapped_column(JSON, default=list)
    notification_status: Mapped[str] = mapped_column(String(64))
    notification_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


# Database engine and session factory
_engine = None
_session_factory = None


def get_engine():
    """Get or create async engine."""
    global _engine
    if _engine is None:
        database_url = settings.database_url
        if not database_url:
            raise RuntimeError("DATABASE_URL not configured")
        _engine = create_async_engine(database_url, echo=settings.database_echo)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or create session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for FastAPI routes to get database session."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db():
    """Initialize database tables."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
