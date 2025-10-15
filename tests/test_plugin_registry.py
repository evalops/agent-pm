import asyncio
from pathlib import Path

import yaml

from agent_pm.clients import jira_client
from agent_pm.plugins.registry import PluginRegistry


def test_ticket_automation_plugin_creates_issue(monkeypatch, tmp_path):
    config_path = tmp_path / "plugins.yaml"
    config = [
        {
            "name": "ticket_automation",
            "module": "agent_pm.plugins.ticket_automation:TicketAutomationPlugin",
            "enabled": True,
            "config": {"project_key": "DEMO", "summary_prefix": "[Plan]"},
        }
    ]
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    registry = PluginRegistry(config_path)
    plugin = registry.get("ticket_automation")
    assert plugin is not None

    calls: list[dict] = []

    async def fake_create_issue(payload):
        calls.append(payload)
        return {"key": "DEMO-1"}

    monkeypatch.setattr(jira_client, "create_issue", fake_create_issue)

    plan: dict[str, object] = {"prd_markdown": "# Plan", "plugins": {}}
    context = {"title": "Roadmap", "requirements": ["Ship MVP"]}
    registry.fire("post_plan", plan=plan, context=context)

    assert calls
    assert calls[0]["fields"]["project"]["key"] == "DEMO"
    assert "ticket_automation" in plan["plugins"]


def test_registry_metadata_includes_disabled(tmp_path):
    config_path = tmp_path / "plugins.yaml"
    config = [
        {
            "name": "disabled_plugin",
            "module": "agent_pm.plugins.ticket_automation:TicketAutomationPlugin",
            "enabled": False,
            "description": "Disabled plug",
        }
    ]
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    registry = PluginRegistry(config_path)
    metadata = registry.list_metadata()
    assert metadata and metadata[0]["enabled"] is False
