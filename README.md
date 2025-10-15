# Agent PM Service

FastAPI service that turns product ideas into structured PRDs, action plans, and operational updates using OpenAI tools plus Jira/GitHub/Slack/Calendar integrations.

## Highlights

- **Idea → Plan:** `/plan` produces PRD markdown, ticket plan, and trace metadata in a single call.
- **Operational guardrails:** Dry-run mode, rate limiting, approvals, and background task queue keep external systems safe.
- **Living PRDs:** Git-style versioning, changelog generation, branching, blame, and approvals for product specs.
- **Observability out of the box:** Structured logs, Prometheus metrics, trace browsing, and cost tracking hooks.

## Quick Start

### Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- OpenAI, Slack, Jira, GitHub, and calendar credentials as needed

### Setup

```bash
git clone https://github.com/evalops/agent-pm.git
cd agent-pm
uv sync --all-extras
cp .env.example .env  # populate secrets before running mutating endpoints
```

### Run locally

```bash
uv run uvicorn app:app --reload
```

The service listens on `http://localhost:8000` by default.

## Everyday Workflows

Plan a single idea:

```bash
curl -s http://localhost:8000/plan \
  -H 'content-type: application/json' \
  -d '{"title":"Improve onboarding","context":"Self-serve users churn","constraints":["<2 weeks"]}'
```

Submit multiple ideas in parallel:

```bash
curl -s http://localhost:8000/plan/batch \
  -H 'content-type: application/json' \
  -d '{"ideas":[{"title":"Idea A"},{"title":"Idea B"}]}'
```

Generate Jira tickets (dry-run returns payloads unless approvals are enabled):

```bash
curl -s http://localhost:8000/ticket \
  -H 'content-type: application/json' \
  -d '{"project_key":"PM","stories":["Draft PRD","Create kickoff deck"]}'
```

PRD versioning essentials:

- `POST /prd/{plan_id}/versions` — commit a new PRD revision.
- `GET /prd/{plan_id}/versions` — list history with diff summaries.
- `GET /prd/{plan_id}/changelog/{from_version}/{to_version}` — AI changelog.
- `POST /prd/{plan_id}/versions/{version_id}/approve` — reviewer sign‑off.

## Operations & Observability

- **Health checks:** `/health` (liveness) and `/health/ready` (deep dependency verification).
- **Metrics:** `/metrics` exposes Prometheus counters and histograms.
- **Traces:** `/operators/traces` and `/operators/traces/{trace}` browse planner traces; async export via webhook or S3 when configured.
- **Task queue:** `/tasks` and `/tasks/{id}` monitor background jobs.
- **Cost tracking:** `agent_pm.cost_tracking` utilities log token and USD usage when responses include usage metadata.

## Configuration Reference

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Required for planner and embedding calls. |
| `DRY_RUN` | Prevents writes to Jira/Slack/GitHub/Calendar when `true`. |
| `API_KEY` / `ADMIN_API_KEY` | Enable API-key auth for user and operator endpoints. |
| `ALLOWED_PROJECTS` | Comma-separated allowlist for `/ticket`. |
| `TRACE_DIR` / `TRACE_EXPORT_*` | Control on-disk traces and optional async exports. |
| `TASK_QUEUE_WORKERS` | Number of background workers handling queued tasks. |
| `LOG_FORMAT` | `json` for structured logging or `text` for local debugging. |

See `config/agents.yaml`, `config/tools.yaml`, and `.env.example` for additional tunables.

## Development

```bash
uv run ruff check .        # lint
uv run ruff format --check .
uv run pytest              # unit tests with coverage in CI
docker compose up          # optional Postgres/Redis/worker stack
```

CI (GitHub Actions) runs lint + tests on every push and pull request.

## Plugin Platform

Agent PM ships with a pluggable automation surface that can notify downstream systems, export telemetry, or react to alignment events. The registry loads plugin definitions from `config/plugins.yaml` and supports live lifecycle management:

- **Registry APIs:** `GET /plugins` returns plugin metadata (enablement state, missing secrets, hook timings, recent errors). `POST /plugins/{name}/enable|disable`, `POST /plugins/{name}/reload`, and `POST /plugins/{name}/config` manage plugin lifecycle and configuration, while `POST /plugins/install` installs new plugin entries by entrypoint or module path. `GET /plugins/discover` enumerates installable plugins exposed via the `agent_pm.plugins` entrypoint group.
- **CLI:** `scripts/manage_plugins.py` mirrors the API—`list`, `enable/disable`, `reload` (global or per-plugin), `update` config, `discover`, and `install` commands emit JSON plus stderr hints for missing secrets or validation errors.
- **Secrets:** Plugins can declare `required_secrets`; the framework resolves them from inline config overrides, environment variables, settings, or an optional secrets file (`PLUGIN_SECRET_PATH`). Missing values surface in `/plugins` responses, CLI output, and the dashboard.
- **Telemetry:** The registry tracks per-hook invocation counts, failures, and rolling timing history, exposing metrics to the API and dashboard for troubleshooting.
- **Dashboard controls:** The Streamlit dashboard (“Plugin Administration” section) displays summary metrics, hook timelines, and offers guarded controls to edit config JSON, reload, or toggle plugins when a Plugins API base URL/key is configured.

For an in-depth walkthrough (configuration schema, lifecycle hooks, discovery flow, and dashboard usage) see [`docs/plugins.md`](./docs/plugins.md).

## Contributing

Please open an issue or pull request with context, and run lint/tests before submitting.

## License

Released under the [MIT License](./LICENSE).
