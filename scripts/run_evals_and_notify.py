"""Run Inspect AI evals and optionally post Slack status."""

import asyncio
from typing import Any

from inspect_ai import run

from agent_pm.clients import slack_client
from agent_pm.settings import settings
from evals.pm_prd_eval import idea_to_prd


def build_summary(result: Any) -> str:
    metrics = getattr(result, "metrics", None)
    if isinstance(metrics, dict):
        lines = ["Inspect AI metrics:"]
        for name, value in metrics.items():
            score = value if isinstance(value, (int, float)) else value
            if isinstance(score, (int, float)):
                lines.append(f"- {name}: {score:.2f}")
            else:
                lines.append(f"- {name}: {score}")
        return "\n".join(lines)
    return f"Inspect AI run completed: {result}"


async def main() -> None:
    outcome = await run(idea_to_prd())
    summary = build_summary(outcome)
    print(summary)
    if settings.dry_run or not slack_client.enabled:
        return
    await slack_client.post_digest(summary)


if __name__ == "__main__":
    asyncio.run(main())
