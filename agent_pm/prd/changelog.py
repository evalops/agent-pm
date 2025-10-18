"""Generate human-readable changelogs from PRD diffs using OpenAI."""

from __future__ import annotations

import logging

from ..openai_utils import get_async_openai_client
from ..settings import settings

logger = logging.getLogger(__name__)


async def generate_changelog(old_prd: str, new_prd: str, diff_summary: dict) -> str:
    """Generate AI-powered changelog, or return a summary stub when running in dry-run mode."""
    prompt = f"""You are a technical writer. Given two versions of a PRD and a diff summary, generate a concise changelog.

Old PRD:
{old_prd[:2000]}

New PRD:
{new_prd[:2000]}

Diff Summary:
- {diff_summary["additions"]} lines added
- {diff_summary["deletions"]} lines removed
- Changed sections: {", ".join(diff_summary.get("changed_sections", []))}

Write a bullet-point changelog highlighting:
1. What was added
2. What was removed
3. What was modified
4. Why these changes matter

Format as markdown bullet points. Keep it under 200 words."""
    client = get_async_openai_client()
    if client is None:
        if settings.dry_run:
            return f"**Changes:** {diff_summary['additions']} additions, {diff_summary['deletions']} deletions"
        raise RuntimeError("OPENAI_API_KEY is required to generate changelog")
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=400,
        )
        changelog = response.choices[0].message.content or "No changes described."
        logger.info("Generated changelog", extra={"length": len(changelog)})
        return changelog
    except Exception as exc:
        logger.error("Failed to generate changelog: %s", exc)
        return f"**Changes:** {diff_summary['additions']} additions, {diff_summary['deletions']} deletions"
