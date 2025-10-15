"""Helpers for alignment analytics dashboards."""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

import requests

from .alignment_log import get_alignment_summary


def fetch_from_api(api_url: str, api_key: str | None, limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    response = requests.get(api_url, params={"limit": limit}, headers=headers, timeout=10)
    response.raise_for_status()
    payload = response.json()
    events = payload.get("events", [])
    summary = payload.get("summary", {})
    return events, summary


def load_alignment_data(
    limit: int = 50,
    api_url: str | None = None,
    api_key: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    api_url = api_url or os.getenv("ALIGNMENTS_API_URL")
    api_key = api_key or os.getenv("ALIGNMENTS_API_KEY")
    if api_url:
        try:
            events, summary = fetch_from_api(api_url, api_key, limit)
            return events, summary, "api"
        except Exception:
            pass

    events, summary = get_alignment_summary(limit)
    return events, summary, "local"


def flatten_alignment_records(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for event in events:
        note = event.get("notification", {}) or {}
        suggestions = event.get("suggestions", []) or [None]
        for suggestion in suggestions:
            record = {
                "event_id": event.get("event_id"),
                "title": event.get("title"),
                "status": note.get("status", "unknown"),
                "channel": note.get("channel"),
                "created_at": event.get("created_at"),
                "idea": None,
                "overlapping_goals": None,
                "similarity": None,
                "slack_link": None,
                "followup_status": (event.get("followup") or {}).get("status"),
                "followup_recorded_at": (event.get("followup") or {}).get("recorded_at"),
            }
            if isinstance(suggestion, dict):
                external = suggestion.get("external_context", {}) or {}
                record.update(
                    {
                        "idea": suggestion.get("idea"),
                        "overlapping_goals": suggestion.get("overlapping_goals", []),
                        "similarity": suggestion.get("similarity"),
                        "external_context": external,
                        "slack_link": external.get("slack_link_hint"),
                        "status_channel": external.get("status_channel"),
                    }
                )
            records.append(record)
    return records


def status_trend_by_day(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for event in events:
        created = event.get("created_at")
        try:
            date_key = datetime.fromisoformat(created).date().isoformat() if created else "unknown"
        except ValueError:
            date_key = "unknown"
        status = (event.get("notification") or {}).get("status", "unknown")
        counts[date_key][status] += 1

    rows: list[dict[str, Any]] = []
    for day in sorted(counts.keys()):
        row = {"date": day}
        row.update(counts[day])
        rows.append(row)
    return rows


def status_counts_by_idea(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, Counter[str]] = defaultdict(Counter)
    for event in events:
        note = event.get("notification") or {}
        status = note.get("status", "unknown")
        for suggestion in event.get("suggestions", []):
            idea = suggestion.get("idea") if isinstance(suggestion, dict) else None
            if idea:
                buckets[idea][status] += 1

    results: list[dict[str, Any]] = []
    for idea, counter in buckets.items():
        total = sum(counter.values())
        results.append({"idea": idea, "total": total, **dict(counter)})

    results.sort(key=lambda item: item["total"], reverse=True)
    return results


def followup_conversion(events: list[dict[str, Any]]) -> dict[str, Any]:
    totals = Counter()
    followups = Counter()
    per_notification: dict[str, Counter[str]] = defaultdict(Counter)

    for event in events:
        notification_status = (event.get("notification") or {}).get("status", "unknown")
        totals[notification_status] += 1
        followup = event.get("followup") or {}
        followup_status = followup.get("status")
        if followup_status:
            followups[followup_status] += 1
            per_notification[notification_status][followup_status] += 1

    rates: dict[str, float] = {}
    overall_total = sum(totals.values())
    total_followups = sum(followups.values())
    if overall_total:
        rates["overall"] = total_followups / overall_total
    for status, count in totals.items():
        if count:
            rates[status] = sum(per_notification.get(status, {}).values()) / count

    return {
        "totals": dict(totals),
        "followup_counts": dict(followups),
        "per_notification": {status: dict(counter) for status, counter in per_notification.items()},
        "rates": rates,
    }


__all__ = [
    "fetch_from_api",
    "load_alignment_data",
    "flatten_alignment_records",
    "status_trend_by_day",
    "status_counts_by_idea",
    "followup_conversion",
]
