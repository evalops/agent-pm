"""Utility to run Inspect AI PRD eval suite."""

import asyncio

from inspect_ai import run

from evals.pm_prd_eval import idea_to_prd


async def main() -> None:
    await run(idea_to_prd())


if __name__ == "__main__":
    asyncio.run(main())
