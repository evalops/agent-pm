"""Inspect AI evaluation suite for PRD generation and revisions."""

import os

import httpx
from inspect_ai import metric, run, task
from inspect_ai.dataset import Sample
from inspect_ai.suite import Suite

API_URL = os.getenv("AGENT_PM_PLAN_URL", "http://localhost:8000/plan")


@metric
def prd_has_sections(output: dict) -> float:
    text = output.get("prd_markdown", "")
    required = [
        "## Context",
        "## Problem",
        "## Goals / Non-Goals",
        "## Acceptance Criteria",
    ]
    return float(all(section in text for section in required))


@metric
def prd_lists_non_goals(output: dict) -> float:
    text = output.get("prd_markdown", "")
    return float("- " in text.split("## Goals / Non-Goals")[-1])


@metric
def status_digest_present(output: dict) -> float:
    digest = output.get("status_digest", "").strip()
    return float(bool(digest))


@metric
def acceptance_has_bullets(output: dict) -> float:
    text = output.get("prd_markdown", "")
    section = text.split("## Acceptance Criteria")[-1]
    return float("- " in section)


@metric
def critic_review_present(output: dict) -> float:
    review = output.get("critic_review")
    return float(isinstance(review, dict) and "status" in review)


@metric
def revision_history_serialized(output: dict) -> float:
    history = output.get("revision_history")
    return float(isinstance(history, list))


@metric
def revision_matches_expectation(output: dict) -> float:
    metadata = output.get("_metadata") or {}
    expect_revision = metadata.get("expect_revision")
    history = output.get("revision_history")
    if expect_revision is None:
        return 1.0
    return float(bool(history) == bool(expect_revision))


@task
def idea_to_prd() -> Suite:
    samples = [
        Sample(
            input={
                "title": "Add in-app eval dashboards",
                "context": "EvalOps users want PRD->eval->release visibility",
                "constraints": ["<2 weeks MVP"],
            },
        ),
        Sample(
            input={
                "title": "Automate stakeholder digests",
                "context": "Execs need weekly summaries of risks and wins",
                "constraints": ["Self-serve approvals"],
            },
        ),
        Sample(
            input={
                "title": "Define activation guardrails",
                "context": "Activation rate is flat; need measurable guardrails",
                "constraints": ["Include quantitative acceptance criteria"],
                "enable_tools": False,
            },
            metadata={"expect_revision": True},
        ),
    ]

    async def execute(sample: Sample) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(API_URL, json=sample.input, timeout=60)
        response.raise_for_status()
        payload = response.json()
        if sample.metadata:
            payload["_metadata"] = sample.metadata
        payload.setdefault("_request", sample.input)
        return payload

    return Suite(
        samples=samples,
        actions=[execute],
        metrics=[
            prd_has_sections,
            prd_lists_non_goals,
            acceptance_has_bullets,
            status_digest_present,
            critic_review_present,
            revision_history_serialized,
            revision_matches_expectation,
        ],
    )


if __name__ == "__main__":
    run(idea_to_prd())
