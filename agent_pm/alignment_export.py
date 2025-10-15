"""Utilities for exporting alignment events."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .alignment_dashboard import flatten_alignment_records
from .metrics import record_alignment_export
from .plugins import plugin_registry

try:
    import boto3  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    boto3 = None


def build_rows(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return flatten_alignment_records(list(events))


def write_csv(path: Path, events: Iterable[dict[str, Any]], *, statuses: set[str] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = build_rows(events)
    if statuses:
        rows = [row for row in rows if row.get("followup_status") in statuses]
    if not rows:
        path.write_text("", encoding="utf-8")
        return path

    fieldnames = sorted({key for row in rows for key in row})

    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    record_alignment_export("csv")
    plugin_registry.fire(
        "post_ticket_export",
        kind="csv",
        destination=str(path),
        rows=len(rows),
        statuses=list(statuses) if statuses else None,
    )
    return path


def upload_csv_to_s3(uri: str, events: Iterable[dict[str, Any]], *, statuses: set[str] | None = None) -> None:
    if boto3 is None:  # pragma: no cover - optional dependency
        raise RuntimeError("boto3 is required for S3 uploads")

    parsed = urlparse(uri)
    if parsed.scheme != "s3":  # pragma: no cover - invalid usage
        raise ValueError("S3 URI must start with s3://")

    bucket = parsed.netloc
    key = parsed.path.lstrip("/")

    rows = build_rows(events)
    if statuses:
        rows = [row for row in rows if row.get("followup_status") in statuses]
    fieldnames = sorted({key for row in rows for key in row})
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    client = boto3.client("s3")
    client.put_object(Bucket=bucket, Key=key, Body=buffer.getvalue().encode("utf-8"), ContentType="text/csv")
    record_alignment_export("s3")
    plugin_registry.fire(
        "post_ticket_export",
        kind="s3",
        destination=uri,
        rows=len(rows),
        statuses=list(statuses) if statuses else None,
    )


__all__ = ["build_rows", "write_csv", "upload_csv_to_s3"]
