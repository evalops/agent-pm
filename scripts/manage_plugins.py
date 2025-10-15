#!/usr/bin/env python3
"""CLI for managing Agent PM plugins."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from agent_pm.plugins import PluginRegistry


def _parse_set(values: list[str] | None) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    if not values:
        return parsed
    for item in values:
        if "=" not in item:
            raise ValueError(f"Invalid config entry {item!r}")
        key, value = item.split("=", 1)
        parsed[key] = value
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Agent PM plugins")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List registered plugins")

    enable_parser = sub.add_parser("enable", help="Enable a plugin")
    enable_parser.add_argument("name", help="Plugin name")

    disable_parser = sub.add_parser("disable", help="Disable a plugin")
    disable_parser.add_argument("name", help="Plugin name")

    reload_parser = sub.add_parser("reload", help="Reload plugin registry")
    reload_parser.add_argument("name", nargs="?", help="Optional plugin name for targeted reload")

    update_parser = sub.add_parser("update", help="Update plugin configuration")
    update_parser.add_argument("name", help="Plugin name")
    update_parser.add_argument(
        "--set",
        metavar="KEY=VALUE",
        action="append",
        help="Configuration key/value pairs",
    )
    sub.add_parser("discover", help="List available plugin entry points")

    install_parser = sub.add_parser("install", help="Install a plugin from an entry point or module reference")
    install_parser.add_argument("--entry-point", dest="entry_point", help="Entry point name (agent_pm.plugins group)")
    install_parser.add_argument("--module", help="Module reference in module:Class format")
    install_parser.add_argument("--name", help="Override plugin name")
    install_parser.add_argument("--enable", action="store_true", help="Enable plugin after installation")
    install_parser.add_argument(
        "--set",
        metavar="KEY=VALUE",
        action="append",
        help="Configuration key/value pairs for the plugin",
    )
    install_parser.add_argument("--description", help="Override plugin description")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    registry = PluginRegistry()

    if args.command == "list":
        metadata = registry.list_metadata()
        for item in metadata:
            name = item.get("name", "unknown")
            for error in item.get("errors", []) or []:
                print(f"[warning] {name}: {error}", file=sys.stderr)
            missing = item.get("secrets", {}).get("missing", []) if item.get("secrets") else []
            if missing:
                print(f"[hint] {name}: missing secrets -> {', '.join(missing)}", file=sys.stderr)
        print(json.dumps({"plugins": metadata}, indent=2))
    elif args.command == "enable":
        metadata = registry.set_enabled(args.name, True)
        print(json.dumps({"plugin": metadata}, indent=2))
    elif args.command == "disable":
        metadata = registry.set_enabled(args.name, False)
        print(json.dumps({"plugin": metadata}, indent=2))
    elif args.command == "reload":
        if getattr(args, "name", None):
            metadata = registry.reload_plugin(args.name)
            print(json.dumps({"plugin": metadata}, indent=2))
        else:
            registry.reload()
            print(json.dumps({"plugins": registry.list_metadata()}, indent=2))
    elif args.command == "update":
        if not args.set:
            parser.error("--set key=value required")
        try:
            config = _parse_set(args.set)
        except ValueError as exc:
            parser.error(str(exc))
        try:
            metadata = registry.update_config(args.name, config)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)
        print(json.dumps({"plugin": metadata}, indent=2))
    elif args.command == "discover":
        entry_points = registry.discover_plugins()
        for item in entry_points:
            if item.get("error"):
                print(f"[warning] {item['entry_point']}: {item['error']}", file=sys.stderr)
        print(json.dumps({"entry_points": entry_points}, indent=2))
    elif args.command == "install":
        module = args.module
        entry_point = args.entry_point
        if not module and not entry_point:
            parser.error("--module or --entry-point required")
        if entry_point and not module:
            catalogue = {item["entry_point"]: item for item in registry.discover_plugins()}
            if entry_point not in catalogue:
                parser.error(f"Unknown entry point {entry_point!r}")
            info = catalogue[entry_point]
            module = info["module"]
            if not args.name:
                args.name = info.get("plugin_name")
            if not args.description and info.get("description"):
                args.description = info["description"]
        try:
            config = _parse_set(args.set)
        except ValueError as exc:
            parser.error(str(exc))
        metadata = registry.install_plugin(
            module,
            name=args.name,
            enabled=args.enable,
            config=config,
            description=args.description,
        )
        print(json.dumps({"plugin": metadata}, indent=2))
    else:  # pragma: no cover - argparse guards
        parser.error(f"Unsupported command {args.command}")


if __name__ == "__main__":
    main()
