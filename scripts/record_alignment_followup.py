#!/usr/bin/env python3
"""CLI helper to record alignment follow-up outcomes."""

from __future__ import annotations

import argparse
import sys

import requests


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record follow-up status for an alignment event"
    )
    parser.add_argument("event_id", help="Alignment event identifier")
    parser.add_argument("status", help="Follow-up status value (e.g., ack, dismissed)")
    parser.add_argument(
        "--api-url",
        default="http://localhost:8000/alignments/{event_id}/followup",
        help="Follow-up endpoint",
    )
    parser.add_argument(
        "--api-key", dest="api_key", help="API key for authentication", default=None
    )
    args = parser.parse_args()

    url = args.api_url.format(event_id=args.event_id)
    headers = {"Content-Type": "application/json"}
    if args.api_key:
        headers["X-API-Key"] = args.api_key

    response = requests.post(
        url, json={"status": args.status}, headers=headers, timeout=10
    )
    if response.status_code >= 400:
        print(
            f"Failed to record follow-up: {response.status_code} {response.text}",
            file=sys.stderr,
        )
        sys.exit(1)

    data = response.json()
    print(f"Recorded follow-up for {data['event_id']}: {data['status']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
