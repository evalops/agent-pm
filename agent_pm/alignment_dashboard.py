"""Helpers for alignment analytics dashboards."""

from __future__ import annotations

import os
from typing import Any, Tuple

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


__all__ = ["fetch_from_api", "load_alignment_data"]
