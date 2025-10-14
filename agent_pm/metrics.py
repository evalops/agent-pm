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


def latest_metrics() -> bytes:
    return generate_latest()


__all__ = [
    "record_planner_request",
    "record_guardrail_rejection",
    "record_revisions",
    "record_tool_invocation",
    "latest_metrics",
]
