import asyncio
import json
from pathlib import Path

import yaml

from agent_pm.clients import jira_client, slack_client
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
    registry.fire("pre_plan", context=context)
    registry.fire("post_plan", plan=plan, context=context)

    assert calls
    assert calls[0]["fields"]["project"]["key"] == "DEMO"
    assert "ticket_automation" in plan["plugins"]
    assert plugin.plan_contexts and plugin.plan_contexts[-1]["title"] == "Roadmap"

    registry.fire("post_alignment_event", event={"event_id": "evt-123"})
    assert "evt-123" in plugin.alignment_events

    registry.fire("post_ticket_export", kind="csv", destination="/tmp/demo.csv", rows=5, statuses=["success"])
    assert plugin.export_events[-1]["kind"] == "csv"


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
    assert metadata[0]["active"] is False


def test_registry_set_enabled_updates_config(tmp_path):
    config_path = tmp_path / "plugins.yaml"
    config = [
        {
            "name": "warehouse_export",
            "module": "agent_pm.plugins.warehouse_export:WarehouseExportPlugin",
            "enabled": False,
            "config": {"path": str(tmp_path / "events.jsonl")},
        }
    ]
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    registry = PluginRegistry(config_path)
    metadata = registry.set_enabled("warehouse_export", True)
    assert metadata["enabled"] is True
    assert registry.is_enabled("warehouse_export") is True

    written = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert written[0]["enabled"] is True

    registry.set_enabled("warehouse_export", False)
    assert registry.is_enabled("warehouse_export") is False


def test_plugin_hook_metrics(monkeypatch, tmp_path):
    config_path = tmp_path / "plugins.yaml"
    config = [
        {
            "name": "ticket_automation",
            "module": "agent_pm.plugins.ticket_automation:TicketAutomationPlugin",
            "enabled": True,
            "config": {"project_key": "METRIC"},
        }
    ]
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    registry = PluginRegistry(config_path)
    plugin = registry.get("ticket_automation")
    assert plugin is not None

    events: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "agent_pm.plugins.registry.record_plugin_hook_invocation",
        lambda plugin_name, hook_name: events.append(("inv", plugin_name, hook_name)),
    )
    monkeypatch.setattr(
        "agent_pm.plugins.registry.record_plugin_hook_failure",
        lambda plugin_name, hook_name: events.append(("fail", plugin_name, hook_name)),
    )

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(plugin, "post_plan", boom)

    registry.fire("post_plan", plan={}, context={})

    assert ("inv", "ticket_automation", "post_plan") in events
    assert ("fail", "ticket_automation", "post_plan") in events


def test_slack_and_warehouse_plugins(monkeypatch, tmp_path):
    config_path = tmp_path / "plugins.yaml"
    config = [
        {
            "name": "slack_followup_alerts",
            "module": "agent_pm.plugins.slack_notifications:SlackAlertsPlugin",
            "enabled": True,
            "config": {"channel": "alerts"},
        },
        {
            "name": "warehouse_export",
            "module": "agent_pm.plugins.warehouse_export:WarehouseExportPlugin",
            "enabled": True,
            "config": {"path": str(tmp_path / "events.jsonl")},
        },
    ]
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    monkeypatch.setattr(slack_client, "token", "x")
    monkeypatch.setattr(slack_client, "channel", "alerts")

    registry = PluginRegistry(config_path)
    slack_plugin = registry.get("slack_followup_alerts")
    warehouse_plugin = registry.get("warehouse_export")
    assert slack_plugin is not None
    assert warehouse_plugin is not None

    captured_messages: list[str] = []

    async def fake_post_digest(message, channel=None):
        captured_messages.append(message)
        return {"ok": True, "channel": channel}

    monkeypatch.setattr("agent_pm.plugins.slack_notifications.slack_client.post_digest", fake_post_digest)
    monkeypatch.setattr(slack_plugin, "_schedule", lambda coro: asyncio.run(coro))

    registry.fire("post_alignment_followup", event={"title": "Alpha", "event_id": "evt-1"}, status="ack")
    registry.fire("post_alignment_event", event={"event_id": "evt-2"})
    registry.fire("on_feedback", feedback={"title": "Alpha", "comment": "Great"})
    registry.fire("post_ticket_export", kind="csv", destination="/tmp/export.csv", rows=2, statuses=["ack"])

    assert len(captured_messages) == 2

    events_file = tmp_path / "events.jsonl"
    assert events_file.exists()
    output_records = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines()]
    assert any(record["event"] == "ticket_export" for record in output_records)
    assert any(record["event"] == "alignment_event" for record in output_records)
    assert any(record["event"] == "feedback" for record in output_records)
