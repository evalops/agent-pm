"""Prometheus metrics instrumentation for Agent PM."""

from __future__ import annotations

from contextlib import contextmanager
from time import perf_counter

from prometheus_client import Counter, Gauge, Histogram, Summary, generate_latest


dead_letter_recorded_total = Counter(
    "task_dead_letter_recorded_total",
    "Dead-letter entries recorded",
    labelnames=("queue", "error_type"),
)

dead_letter_requeued_total = Counter(
    "task_dead_letter_requeued_total",
    "Dead-letter entries requeued",
    labelnames=("queue", "error_type"),
)

dead_letter_purged_total = Counter(
    "task_dead_letter_purged_total",
    "Dead-letter entries purged",
    labelnames=("queue", "mode"),
)

dead_letter_active_gauge = Gauge(
    "task_dead_letter_active",
    "Current count of dead-letter entries",
    labelnames=("queue",),
)

dead_letter_auto_requeue_total = Counter(
    "task_dead_letter_auto_requeue_total",
    "Dead-letter entries automatically requeued",
    labelnames=("queue", "error_type"),
)

dead_letter_alert_total = Counter(
    "task_dead_letter_alert_total",
    "Dead-letter alert notifications",
    labelnames=("queue", "error_type"),
)

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

task_queue_enqueued_total = Counter(
    "task_queue_enqueued_total",
    "Tasks enqueued to background queue",
    labelnames=("queue",),
)

task_queue_completed_total = Counter(
    "task_queue_completed_total",
    "Tasks completed grouped by status",
    labelnames=("queue", "status"),
)

task_queue_latency_seconds = Histogram(
    "task_queue_latency_seconds",
    "Task execution latency in seconds",
    labelnames=("queue",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)

plugin_hook_invocations_total = Counter(
    "plugin_hook_invocations_total",
    "Plugin hook invocation counts",
    labelnames=("plugin", "hook"),
)

plugin_hook_failures_total = Counter(
    "plugin_hook_failures_total",
    "Plugin hook failures",
    labelnames=("plugin", "hook"),
)

client_requests_total = Counter(
    "client_requests_total",
    "External client calls grouped by client and outcome",
    labelnames=("client", "outcome"),
)

client_latency_seconds = Summary(
    "client_latency_seconds",
    "Latency of external client calls grouped by client",
    labelnames=("client",),
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


def record_plugin_hook_invocation(plugin: str, hook: str) -> None:
    plugin_hook_invocations_total.labels(plugin=plugin, hook=hook).inc()


def record_plugin_hook_failure(plugin: str, hook: str) -> None:
    plugin_hook_failures_total.labels(plugin=plugin, hook=hook).inc()


@contextmanager
def record_client_call(client: str):
    """Context manager that records metrics around an external client invocation."""

    start = perf_counter()
    outcome = "success"
    try:
        yield
    except Exception:
        outcome = "error"
        raise
    finally:
        client_requests_total.labels(client=client, outcome=outcome).inc()
        client_latency_seconds.labels(client=client).observe(perf_counter() - start)


def record_task_enqueued(queue: str) -> None:
    task_queue_enqueued_total.labels(queue=queue).inc()


def record_task_completion(queue: str, status: str) -> None:
    task_queue_completed_total.labels(queue=queue, status=status).inc()


def record_task_latency(queue: str, duration: float) -> None:
    task_queue_latency_seconds.labels(queue=queue).observe(duration)


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
    "record_plugin_hook_invocation",
    "record_plugin_hook_failure",
    "record_client_call",
    "record_task_enqueued",
    "record_task_completion",
    "record_task_latency",
    "latest_metrics",
    "dead_letter_recorded_total",
    "dead_letter_requeued_total",
    "dead_letter_purged_total",
    "dead_letter_active_gauge",
    "dead_letter_auto_requeue_total",
    "dead_letter_alert_total",
]
