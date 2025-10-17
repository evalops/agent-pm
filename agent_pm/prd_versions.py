"""PRD version control - git-like operations for product specs."""

from __future__ import annotations

import difflib
import hashlib
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_pm.database import PRDApproval, PRDVersion
from agent_pm.utils.datetime import utc_now_isoformat

logger = logging.getLogger(__name__)


def compute_version_hash(content: str, parent_id: str | None) -> str:
    """Compute SHA-like hash for PRD version (git-style)."""
    data = f"{content}{parent_id or 'root'}{utc_now_isoformat()}"
    return hashlib.sha256(data.encode()).hexdigest()[:16]


def compute_diff(old_content: str, new_content: str) -> dict[str, Any]:
    """Compute unified diff between two PRD versions."""
    old_lines = old_content.splitlines()
    new_lines = new_content.splitlines()

    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))

    # Count changes
    additions = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))

    return {
        "additions": additions,
        "deletions": deletions,
        "diff_lines": diff,
        "changed_sections": _extract_changed_sections(diff),
    }


def _extract_changed_sections(diff_lines: list[str]) -> list[str]:
    """Extract which sections changed (Goals, Requirements, etc.)."""
    sections = []
    for line in diff_lines:
        if line.startswith("+## ") or line.startswith("-## "):
            section = line[4:].strip()
            if section not in sections:
                sections.append(section)
    return sections


async def create_version(
    session: AsyncSession,
    plan_id: str,
    prd_markdown: str,
    author: str | None = None,
    author_email: str | None = None,
    commit_message: str | None = None,
    parent_version_id: str | None = None,
    branch: str = "main",
) -> PRDVersion:
    """Create new PRD version (like git commit)."""
    version_id = compute_version_hash(prd_markdown, parent_version_id)

    # Compute diff if parent exists
    diff_summary = None
    if parent_version_id:
        result = await session.execute(select(PRDVersion).where(PRDVersion.version_id == parent_version_id))
        parent = result.scalar_one_or_none()
        if parent:
            diff_summary = compute_diff(parent.prd_markdown, prd_markdown)

    version = PRDVersion(
        version_id=version_id,
        plan_id=plan_id,
        parent_version_id=parent_version_id,
        branch=branch,
        prd_markdown=prd_markdown,
        commit_message=commit_message or "Update PRD",
        author=author,
        author_email=author_email,
        diff_summary=diff_summary,
    )
    session.add(version)
    await session.commit()
    await session.refresh(version)

    logger.info(
        "Created PRD version",
        extra={
            "version_id": version_id,
            "plan_id": plan_id,
            "branch": branch,
            "parent": parent_version_id,
        },
    )
    return version


async def get_version_history(
    session: AsyncSession,
    plan_id: str,
    branch: str = "main",
    limit: int = 50,
) -> list[PRDVersion]:
    """Get version history for a PRD (like git log)."""
    result = await session.execute(
        select(PRDVersion)
        .where(PRDVersion.plan_id == plan_id, PRDVersion.branch == branch)
        .order_by(PRDVersion.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def create_branch(
    session: AsyncSession,
    plan_id: str,
    source_version_id: str,
    new_branch_name: str,
    author: str | None = None,
) -> PRDVersion:
    """Create new branch from existing version (like git checkout -b)."""
    result = await session.execute(select(PRDVersion).where(PRDVersion.version_id == source_version_id))
    source = result.scalar_one()

    # Create new version on new branch
    return await create_version(
        session=session,
        plan_id=plan_id,
        prd_markdown=source.prd_markdown,
        author=author,
        commit_message=f"Branch from {source.branch}",
        parent_version_id=source_version_id,
        branch=new_branch_name,
    )


async def get_blame(session: AsyncSession, version_id: str) -> dict[str, Any]:
    """Get blame info: which author changed which sections."""
    result = await session.execute(select(PRDVersion).where(PRDVersion.version_id == version_id))
    version = result.scalar_one()

    # Build blame by walking parent chain
    blame_map: dict[str, dict] = {}
    current = version

    while current:
        sections = _parse_sections(current.prd_markdown)
        for section_name, _content in sections.items():
            if section_name not in blame_map:
                blame_map[section_name] = {
                    "author": current.author or "unknown",
                    "version_id": current.version_id,
                    "created_at": current.created_at.isoformat(),
                    "commit_message": current.commit_message,
                }

        # Walk to parent
        if not current.parent_version_id:
            break
        result = await session.execute(select(PRDVersion).where(PRDVersion.version_id == current.parent_version_id))
        current = result.scalar_one_or_none()

    return blame_map


def _parse_sections(markdown: str) -> dict[str, str]:
    """Parse markdown into sections by ## headers."""
    sections = {}
    current_section = None
    current_content = []

    for line in markdown.splitlines():
        if line.startswith("## "):
            if current_section:
                sections[current_section] = "\n".join(current_content)
            current_section = line[3:].strip()
            current_content = []
        elif current_section:
            current_content.append(line)

    if current_section:
        sections[current_section] = "\n".join(current_content)

    return sections


async def request_approval(
    session: AsyncSession,
    version_id: str,
    reviewer: str,
    reviewer_email: str | None = None,
) -> PRDApproval:
    """Request approval for PRD version (like GitHub PR review request)."""
    approval = PRDApproval(
        version_id=version_id,
        reviewer=reviewer,
        reviewer_email=reviewer_email,
        status="pending",
    )
    session.add(approval)
    await session.commit()
    await session.refresh(approval)
    return approval


async def approve_version(
    session: AsyncSession,
    version_id: str,
    reviewer: str,
    comments: str | None = None,
) -> PRDVersion:
    """Approve PRD version and mark as ready."""
    # Create approval record
    approval = PRDApproval(
        version_id=version_id,
        reviewer=reviewer,
        status="approved",
        comments=comments,
    )
    session.add(approval)

    # Update version status
    result = await session.execute(select(PRDVersion).where(PRDVersion.version_id == version_id))
    version = result.scalar_one()
    version.status = "approved"

    await session.commit()
    await session.refresh(version)
    return version
