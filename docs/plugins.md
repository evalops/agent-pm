# Plugin Platform

Agent PM exposes a plugin framework that lets you extend automation workflows without modifying the core service. This guide covers configuration, secret management, lifecycle controls, and the operator tooling that shipped with the plugin overhaul.

## Architecture Overview

Plugins subclass `agent_pm.plugins.base.PluginBase` and are registered in a YAML manifest (`config/plugins.yaml`). The registry loads each entry, validates configuration via Pydantic models, and wires hook callbacks into alignment, planning, and export events. Plugins can:

- receive lifecycle notifications (`on_enable`, `on_disable`, `on_reload`)
- contribute FastAPI routers (for custom endpoints)
- emit cross-plugin events through `PluginBase.emit`
- surface telemetry (invocation counts, failure totals, timings) exposed by the registry

## Configuration Manifest

The manifest is a list of plugin entries:

```yaml
- name: ticket_automation
  module: agent_pm.plugins.ticket_automation:TicketAutomationPlugin
  enabled: true
  description: Create Jira issues from generated plans
  hooks:
    - post_plan
    - post_alignment_event
  config:
    project_key: DEMO
    watchers: []
    secrets:
      JIRA_API_TOKEN:
        env: JIRA_API_TOKEN
```

Fields:

| Field | Description |
| --- | --- |
| `name` | Unique identifier surfaced in APIs/CLI. Defaults to plugin class `name` attribute if omitted during install. |
| `module` | Import path in `module:ClassName` format. Required. |
| `enabled` | Whether the plugin is active on load; can be toggled later. |
| `description` | Optional human-readable summary. |
| `hooks` | Optional explicit list; otherwise derived from plugin class. |
| `config` | Arbitrary plugin configuration merged into `plugin.config`. `config.secrets` (optional) supplies inline secret overrides (see below). |

Invalid entries and validation errors are captured and returned in `/plugins` responses with the `invalid` flag and error details so operators can fix manifest issues without crashing the service.

## Secret Resolution

`PluginBase.required_secrets` enumerates the credential keys a plugin expects. The registry resolves each secret in the following order:

1. Inline overrides under `config.secrets` (supports literal values or `{ "env": "ENV_NAME" }`).
2. Environment variables matching the secret key.
3. Settings attributes (environment-driven configuration) on `agent_pm.settings.Settings` (e.g., `SLACK_BOT_TOKEN` → `settings.slack_bot_token`).
4. Optional secrets file referenced by `PLUGIN_SECRET_PATH` (`.yaml`, `.yml`, or `.json`). The file accepts top-level keys, a `global` section, or scoped sections under `plugins.<plugin_name>`.

Missing secrets are surfaced via:

- `/plugins` metadata (`secrets.missing` array)
- CLI `list` warnings
- Streamlit dashboard controls (displayed in the Plugin Administration panel)

## Lifecycle Hooks

`PluginBase` defines optional lifecycle methods:

- `on_enable()` — invoked on registry load, toggling from disabled → enabled, and targeted reload.
- `on_disable()` — called when a previously-enabled plugin is disabled or removed.
- `on_reload()` — runs after module reloads (hot code updates) to refresh resources.

Use these hooks to hydrate SDK clients with resolved secrets, warm caches, or tear down connections. The registry now automatically calls `on_enable()` for active plugins during bootstrap, ensuring secrets propagate before hooks execute.

## Discovery & Installation

Plugins can be distributed as Python packages exposing an entry point in the `agent_pm.plugins` group. The registry and CLI leverage `importlib.metadata.entry_points` to discover installable plugins alongside their metadata.

- `GET /plugins/discover` — returns catalog of entry points (`entry_point`, `module`, `plugin_name`, description, hooks, errors).
- `scripts/manage_plugins.py discover` — CLI equivalent with stderr warnings for load failures.
- `POST /plugins/install` — installs a plugin by entry point or explicit module path (optionally enabling it immediately and seeding config/description).
- `scripts/manage_plugins.py install --entry-point demo --enable --set key=value` — CLI installation helper.

For per-plugin hot reloads (useful during development), call `POST /plugins/{name}/reload` or `scripts/manage_plugins.py reload <name>`; the registry invalidates import caches, reloads the module, and invokes lifecycle hooks.

## Telemetry & Troubleshooting

The registry tracks per-hook metrics and historical samples:

- `hook_stats` includes `invocations`, `failures`, `total_duration_ms`, `last_duration_ms`, and `avg_duration_ms` per hook.
- `hook_history` maintains a rolling deque (last 100 entries per hook) with timestamp, status (`success`/`error`), duration, and error message (if applicable).
- `errors` retains the last 10 registry or lifecycle errors per plugin, including instantiation failures.

This data powers `/plugins` responses, the CLI warnings, and the dashboard timeline charts to accelerate debugging.

## Operator Tooling

### FastAPI Endpoints

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/plugins` | GET | Summaries, hook metrics, missing secrets, errors. |
| `/plugins/discover` | GET | Entry-point catalog. |
| `/plugins/install` | POST | Install new plugin definitions. |
| `/plugins/{name}/enable` | POST | Toggle on. |
| `/plugins/{name}/disable` | POST | Toggle off. |
| `/plugins/{name}/reload` | POST | Reload a single plugin and re-run lifecycle hooks. |
| `/plugins/{name}/config` | POST | Update plugin configuration (validated). |
| `/plugins/reload` | POST | Reload entire registry (re-reads manifest, re-mounts routers). |

All plugin endpoints require operator authentication (API key dependencies).

### CLI (`uv run python scripts/manage_plugins.py ...`)

- `list` — prints registry metadata, writes warnings/hints for errors and missing secrets.
- `enable <name>` / `disable <name>` — toggles plugin enablement.
- `reload [name]` — reloads registry or individual plugin.
- `update <name> --set key=value ...` — updates config keys.
- `discover` — entry-point discovery (same data as API).
- `install --entry-point demo` or `install --module package.module:Class` — install new plugin definitions with optional `--name`, `--enable`, `--description`, `--set key=value`.

### Streamlit Dashboard (`scripts/streamlit_alignments.py`)

When `PLUGINS_API_URL` (and optional `PLUGINS_API_KEY`) are configured, the dashboard shows:

- Summary table with enablement, invocation counts, failure totals, average hook duration, missing secrets, and recent errors.
- Hook-level metrics table plus aggregate invocation bar chart.
- Timeline chart and raw history table for a selected plugin/hook pair.
- Administration controls within expanders: edit config JSON, reload plugin, enable/disable plugin.

## Writing a Plugin

1. Subclass `PluginBase`:

   ```python
   from agent_pm.plugins.base import PluginBase

   class MyPlugin(PluginBase):
       name = "my_plugin"
       hooks = ("post_plan",)
       required_secrets = ("MY_API_TOKEN",)

       def on_enable(self):
           self.token = self.get_secret("MY_API_TOKEN")

       def post_plan(self, plan: dict[str, Any], context: dict[str, Any]):
           if not self.token:
               return
           ...
   ```

2. Export the class via `agent_pm.plugins.__all__` or declare an entry point in your package:

   ```toml
   [project.entry-points."agent_pm.plugins"]
   my-plugin = "my_package.plugins:MyPlugin"
   ```

3. Add an entry to `config/plugins.yaml` (or use the install API/CLI) and provide any required secrets.

4. Reload the registry (`scripts/manage_plugins.py reload`) or start the service; verify status via `/plugins` or the dashboard.

With these tools in place, plugins can evolve independently, gain telemetry-driven insights, and be administered without redeploying the core API.
