#!/usr/bin/env python3
"""Export alignment events to CSV or S3."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_pm.alignment_dashboard import load_alignment_data
from agent_pm.alignment_export import upload_csv_to_s3, write_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Export alignment insights")
    parser.add_argument(
        "--limit", type=int, default=100, help="Number of events to export"
    )
    parser.add_argument(
        "--api-url", dest="api_url", help="Alignments API URL", default=None
    )
    parser.add_argument(
        "--api-key",
        dest="api_key",
        help="API key for authenticated requests",
        default=None,
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Local CSV output path",
        default=Path("alignment_export.csv"),
    )
    parser.add_argument(
        "--s3-uri",
        dest="s3_uri",
        help="Optional s3://bucket/key destination",
        default=None,
    )
    parser.add_argument(
        "--followup-status",
        dest="followup_status",
        action="append",
        help="Filter events by follow-up status (can be repeated)",
    )
    args = parser.parse_args()

    events, summary, source = load_alignment_data(
        limit=args.limit, api_url=args.api_url, api_key=args.api_key
    )
    print(f"Exporting {len(events)} events from {source}")
    statuses = set(args.followup_status or []) or None

    if args.output:
        path = write_csv(args.output, events, statuses=statuses)
        print(f"CSV written to {path}")

    if args.s3_uri:
        upload_csv_to_s3(args.s3_uri, events, statuses=statuses)
        print(f"Uploaded CSV to {args.s3_uri}")

    print(f"Summary: {summary}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        print(f"Export failed: {exc}", file=sys.stderr)
        sys.exit(1)
