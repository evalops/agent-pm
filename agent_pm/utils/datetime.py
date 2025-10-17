"""
Utilities for working with timezone-aware UTC datetimes.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return a timezone-aware datetime in UTC."""
    return datetime.now(UTC)


def utc_now_isoformat(with_z_suffix: bool = True) -> str:
    """Return an ISO 8601 timestamp for the current UTC time."""
    value = utc_now().isoformat()
    if with_z_suffix:
        return value.replace("+00:00", "Z")
    return value


__all__ = ["utc_now", "utc_now_isoformat"]
