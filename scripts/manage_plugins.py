#!/usr/bin/env python3
"""CLI for managing Agent PM plugins."""

from __future__ import annotations

import argparse
import json

from agent_pm.plugins import PluginRegistry


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Agent PM plugins")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List registered plugins")

    enable_parser = sub.add_parser("enable", help="Enable a plugin")
    enable_parser.add_argument("name", help="Plugin name")

    disable_parser = sub.add_parser("disable", help="Disable a plugin")
    disable_parser.add_argument("name", help="Plugin name")

    sub.add_parser("reload", help="Reload plugin registry from config")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    registry = PluginRegistry()

    if args.command == "list":
        print(json.dumps({"plugins": registry.list_metadata()}, indent=2))
    elif args.command == "enable":
        metadata = registry.set_enabled(args.name, True)
        print(json.dumps({"plugin": metadata}, indent=2))
    elif args.command == "disable":
        metadata = registry.set_enabled(args.name, False)
        print(json.dumps({"plugin": metadata}, indent=2))
    elif args.command == "reload":
        registry.reload()
        print(json.dumps({"plugins": registry.list_metadata()}, indent=2))
    else:  # pragma: no cover - argparse guards
        parser.error(f"Unsupported command {args.command}")


if __name__ == "__main__":
    main()
