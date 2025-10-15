"""Prometheus metrics instrumentation for Agent PM."""

from __future__ import annotations

from contextlib import contextmanager
from time import perf_counter

from prometheus_client import Counter, Histogram, generate_latest

planner_requests_total = Counter(
    "planner_requests_total",
    "Total planner invocations",
)

planner_guardrail_rejections_total = Counter(
    "planner_guardrail_rejections_total",
    "Planner requests rejected by guardrails",
    labelnames=("reason",),
)

planner_revisions_total = Counter(
    "planner_revisions_total",
    "Total critic-triggered revisions",
)

planner_duration_seconds = Histogram(
    "planner_duration_seconds",
    "Planner end-to-end duration in seconds",
    buckets=(0.5, 1, 2, 3, 5, 8, 13),
)

tool_invocations_total = Counter(
    "tool_invocations_total",
    "Tool invocation counts grouped by result",
    labelnames=("tool", "result"),
)

dspy_guidance_total = Counter(
    "dspy_guidance_total",
    "DSPy guidance usage grouped by outcome",
    labelnames=("outcome",),
)

alignment_notification_total = Counter(
    "alignment_notification_total",
    "Goal alignment notifications grouped by status",
    labelnames=("status",),
)

alignment_followup_total = Counter(
    "alignment_followup_total",
    "Goal alignment follow-up outcomes",
    labelnames=("status",),
)

alignment_exports_total = Counter(
    "alignment_exports_total",
    "Alignment export operations grouped by kind",
    labelnames=("kind",),
)

alignment_feedback_total = Counter(
    "alignment_feedback_total",
    "Feedback submissions grouped by source",
    labelnames=("source",),
)


@contextmanager
def record_planner_request() -> None:
    start = perf_counter()
    planner_requests_total.inc()
    try:
        yield
    finally:
        planner_duration_seconds.observe(perf_counter() - start)


def record_guardrail_rejection(reason: str) -> None:
    planner_guardrail_rejections_total.labels(reason=reason).inc()


def record_revisions(count: int) -> None:
    if count <= 0:
        return
    planner_revisions_total.inc(count)


def record_tool_invocation(tool: str, result: str) -> None:
    tool_invocations_total.labels(tool=tool, result=result).inc()


def record_dspy_guidance(outcome: str) -> None:
    dspy_guidance_total.labels(outcome=outcome).inc()


def record_alignment_notification(status: str) -> None:
    alignment_notification_total.labels(status=status).inc()


def record_alignment_followup(status: str) -> None:
    alignment_followup_total.labels(status=status).inc()


def record_alignment_export(kind: str) -> None:
    alignment_exports_total.labels(kind=kind).inc()


def record_feedback_submission(source: str) -> None:
    alignment_feedback_total.labels(source=source).inc()


def latest_metrics() -> bytes:
    return generate_latest()


__all__ = [
    "record_planner_request",
    "record_guardrail_rejection",
    "record_revisions",
    "record_tool_invocation",
    "record_dspy_guidance",
    "record_alignment_notification",
    "record_alignment_followup",
    "record_alignment_export",
    "record_feedback_submission",
    "latest_metrics",
]
