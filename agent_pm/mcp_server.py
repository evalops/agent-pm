"""Hermes MCP server — exposes Agent PM as MCP tools for Hermes and other agents.

Minimal stdio JSON-RPC MCP server. When Hermes connects via MCP,
it can call agent-pm procedures, scan Sentry, query Linear, etc.
as tools instead of hitting REST endpoints.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)

# Tool definitions exposed to MCP clients
TOOL_DEFINITIONS = [
    {
        "name": "agent_pm_run_procedure",
        "description": "Run a named procedure (e.g. weekly_progress_review, dependabot_triage, agent_pr_security_scan, deploy_readiness).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Procedure name (stem of YAML file in procedures/)."},
                "dry_run": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, don't mutate external systems.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "agent_pm_sentry_scan",
        "description": "Scan Sentry for unresolved issues, error counts, or search events.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "default": "is:unresolved", "description": "Sentry issue search query."},
                "stats_period": {"type": "string", "default": "14d", "description": "Time window (1h, 24h, 7d, 14d)."},
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "agent_pm_linear_scan",
        "description": "Query Linear for issues — stale detection, status sweeps, team listing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list_issues", "list_teams", "stale_sweep"]},
                "team_id": {"type": "string", "description": "Linear team ID to scope to."},
                "state": {"type": "string", "description": "Workflow state name filter (e.g. 'Todo', 'In Progress')."},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["action"],
        },
    },
    {
        "name": "agent_pm_github_pr_scan",
        "description": "Scan GitHub PRs across configured repos — CI status, mergeability, security bumps.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "org": {"type": "string", "default": "evalops", "description": "GitHub org to scan."},
                "author": {"type": "string", "description": "Filter by PR author (e.g. 'dependabot')."},
                "state": {"type": "string", "default": "open"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "agent_pm_list_procedures",
        "description": "List all available procedure definitions.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


async def _run_procedure(name: str, dry_run: bool) -> dict[str, Any]:
    """Execute a procedure and return the result."""
    from agent_pm.procedure_runner import execute_procedure
    from agent_pm.procedures import loader

    procedures = loader.load()
    if name not in procedures:
        return {"error": f"Procedure '{name}' not found", "available": list(procedures.keys())}

    try:
        return await execute_procedure(name, dry_run=dry_run)
    except Exception as exc:
        return {"error": str(exc), "procedure": name}


async def _sentry_scan(query: str, stats_period: str, limit: int) -> dict[str, Any]:
    """Run a Sentry issue scan."""
    from agent_pm.connectors.sentry import sentry_connector

    try:
        issues = await sentry_connector.list_issues(query=query, stats_period=stats_period, limit=limit)
        return {"issues": issues, "count": len(issues), "query": query}
    except Exception as exc:
        return {"error": str(exc)}


async def _linear_scan(action: str, team_id: str | None, state: str | None, limit: int) -> dict[str, Any]:
    """Query Linear."""
    from agent_pm.connectors.linear import linear_connector

    try:
        if action == "list_teams":
            teams = await linear_connector.list_teams()
            return {"teams": teams}
        elif action == "stale_sweep":
            issues = await linear_connector.list_issues(
                team_id=team_id,
                state=state,
                order_by="updatedAt",
                limit=limit,
            )
            # Flag stale items
            from datetime import UTC, datetime, timedelta

            now = datetime.now(tz=UTC)
            stale = []
            for issue in issues:
                updated = issue.get("updatedAt")
                due = issue.get("dueDate")
                flags = []
                if due and due < now.date().isoformat():
                    flags.append("past_due")
                if updated:
                    updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    if (now - updated_dt) > timedelta(days=2):
                        flags.append("stale")
                if flags:
                    stale.append(
                        {"id": issue["id"], "identifier": issue["identifier"], "title": issue["title"], "flags": flags}
                    )
            return {"total": len(issues), "stale": stale}
        else:
            issues = await linear_connector.list_issues(team_id=team_id, state=state, limit=limit)
            return {"issues": issues, "count": len(issues)}
    except Exception as exc:
        return {"error": str(exc)}


async def _github_pr_scan(org: str, author: str | None, state: str, limit: int) -> dict[str, Any]:
    """Scan GitHub PRs."""
    from agent_pm.settings import settings

    try:
        # Use the existing GitHub connector
        import httpx

        headers = {
            "Authorization": f"Bearer {settings.github_token}",
            "Accept": "application/vnd.github+json",
        }
        repos = settings.github_repositories
        params: dict[str, Any] = {"per_page": limit, "state": state}
        query_parts = ["is:pr", f"state:{state}"]
        if author:
            if repos:
                query_parts.extend(f"repo:{repo}" for repo in repos)
            else:
                query_parts.append(f"org:{org}")
            query_parts.append(f"author:{author}")
            params["q"] = " ".join(query_parts)
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.github.com/search/issues",
                    headers=headers,
                    params=params,
                    timeout=30,
                )
            resp.raise_for_status()
            data = resp.json()
            return {"prs": data.get("items", []), "total": data.get("total_count", 0)}
        else:
            # Use connector for org repos
            repos = repos or ["evalops/platform", "evalops/deploy", "evalops/maestro-internal"]
            all_prs = []
            for repo in repos:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"https://api.github.com/repos/{repo}/pulls",
                        headers=headers,
                        params={"state": state, "per_page": limit},
                        timeout=30,
                    )
                if resp.status_code == 200:
                    all_prs.extend(resp.json())
            return {"prs": all_prs, "total": len(all_prs)}
    except Exception as exc:
        return {"error": str(exc)}


async def _list_procedures() -> dict[str, Any]:
    from agent_pm.procedures import loader

    procedures = loader.load()
    summaries = {}
    for name, proc in procedures.items():
        summaries[name] = {
            "description": proc.get("description", ""),
            "steps": len(proc.get("steps", [])),
            "schedule": proc.get("schedule"),
        }
    return {"procedures": summaries}


# Tool dispatch
TOOL_HANDLERS = {
    "agent_pm_run_procedure": lambda args: _run_procedure(args.get("name", ""), args.get("dry_run", False)),
    "agent_pm_sentry_scan": lambda args: _sentry_scan(
        args.get("query", "is:unresolved"),
        args.get("stats_period", "14d"),
        args.get("limit", 10),
    ),
    "agent_pm_linear_scan": lambda args: _linear_scan(
        args.get("action", "list_issues"),
        args.get("team_id"),
        args.get("state"),
        args.get("limit", 50),
    ),
    "agent_pm_github_pr_scan": lambda args: _github_pr_scan(
        args.get("org", "evalops"),
        args.get("author"),
        args.get("state", "open"),
        args.get("limit", 20),
    ),
    "agent_pm_list_procedures": lambda args: _list_procedures(),
}


async def handle_request(request: dict[str, Any]) -> dict[str, Any]:
    """Handle a JSON-RPC request."""
    req_id = request.get("id")
    method = request.get("method", "")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "agent-pm-mcp", "version": "0.1.0"},
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOL_DEFINITIONS},
        }

    if method == "tools/call":
        params = request.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }

        try:
            result = await handler(arguments)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result)}]},
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32000, "message": str(exc)},
            }

    if method == "notifications/initialized":
        return {}  # No response for notifications

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


async def serve_stdio() -> None:
    """Run the MCP server over stdio (stdin/stdout)."""
    logger.info("Agent PM MCP server starting on stdio")
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            request = json.loads(line)
            response = await handle_request(request)
            if response:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
        except json.JSONDecodeError:
            continue
        except EOFError:
            break
        except Exception:
            logger.exception("MCP server error")
            continue


def main() -> None:
    asyncio.run(serve_stdio())


if __name__ == "__main__":
    main()

__all__ = ["serve_stdio", "handle_request", "TOOL_DEFINITIONS", "main"]
