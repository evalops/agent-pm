"""Generate human-readable changelogs from PRD diffs using OpenAI."""

from __future__ import annotations

import logging

from openai import AsyncOpenAI

from agent_pm.settings import settings

logger = logging.getLogger(__name__)


async def generate_changelog(old_prd: str, new_prd: str, diff_summary: dict) -> str:
    """Generate AI-powered changelog describing changes between PRD versions."""
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
    api_key = settings.openai_api_key
    if not api_key:
        if settings.dry_run:
            return f"**Changes:** {diff_summary['additions']} additions, {diff_summary['deletions']} deletions"
        raise RuntimeError("OPENAI_API_KEY is required to generate changelog")

    client = AsyncOpenAI(api_key=api_key)
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
