#!/usr/bin/env python3
"""CLI utility to summarize goal alignment analytics."""

from __future__ import annotations

import argparse
import json

from agent_pm.alignment_log import get_alignment_summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize goal alignment events")
    parser.add_argument("--limit", type=int, default=50, help="Number of recent events to include")
    parser.add_argument(
        "--format",
        choices={"text", "json"},
        default="text",
        help="Output format",
    )
    args = parser.parse_args()

    events, summary = get_alignment_summary(limit=args.limit)

    if args.format == "json":
        print(json.dumps({"summary": summary, "events": events}, indent=2))
        return

    print(f"Alignment Summary (last {len(events)} events)")
    print("Status counts:")
    for status, count in sorted(summary["status_counts"].items()):
        print(f"  {status}: {count}")

    if summary["top_ideas"]:
        print("Top overlapping initiatives:")
        for idea, count in summary["top_ideas"]:
            print(f"  {idea}: {count}")


if __name__ == "__main__":
    main()
