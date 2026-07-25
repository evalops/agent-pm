# Agent PM

A Slack-native bot that turns product ideas into PRDs and tickets. Post an idea (app mention or DM) → a [pi](https://github.com/earendil-works/pi) agent drafts a structured PRD in the thread → a human approves with a ✅ reaction → the bot files one GitHub issue per user story and posts the links.

This is a full rewrite of the previous Python/FastAPI service, which is preserved on the [`archive/python-legacy`](https://github.com/evalops/agent-pm/tree/archive/python-legacy) branch.

## How it works

1. **Draft** — mention the bot (`@Agent PM an idea for…`) or DM it. The bot posts a "drafting…" placeholder, runs the agent, and updates the message with the PRD draft plus a "React with ✅ to file N issues" line. The bot seeds its own ✅ reaction for one-click approval.
2. **Revise (optional)** — reply in the same thread with follow-ups ("make the non-goals stricter"). The bot fetches the thread history and produces a revised draft, which replaces the pending one. Only the latest draft in a thread is approve-able; approving a superseded draft gets a "stale draft" note.
3. **Approve** — a human adds ✅ to the latest draft. The bot files one GitHub issue per user story (deterministic code, not the LLM) and replies with the results: "filed X of N", issue links, and any per-story failures.

Nothing is ever filed without an explicit human approval. `DRY_RUN` (default `true`) is a global safety override on top of that: even approved drafts only produce would-be issue payloads in the thread. Set `DRY_RUN=false` to enable real issue creation.

**Caveat:** pending drafts live in memory. A process restart loses them — old draft messages will simply no longer be approve-able.

## Quick start

1. **Create the Slack app** (at [api.slack.com/apps](https://api.slack.com/apps)):
   - Enable **Socket Mode** and create an app-level token with the `connections:write` scope → `SLACK_APP_TOKEN` (`xapp-...`).
   - Under **OAuth & Permissions**, add bot scopes: `app_mentions:read`, `chat:write`, `reactions:read`, `reactions:write`, `im:history`, `im:read`, `im:write`. Install the app to your workspace → `SLACK_BOT_TOKEN` (`xoxb-...`).
   - Under **Event Subscriptions**, subscribe to the bot events `app_mention`, `message.im`, and `reaction_added`.
2. **GitHub token**: a token (fine-grained PAT or GitHub App token) with **Issues: write** on the target repository.
3. **Configure and run**:

   ```sh
   bun install
   cp .env.example .env   # fill in the values
   bun run dev            # or: bun start
   ```

## Configuration

| Variable           | Required | Default          | Description                                                                                             |
| ------------------ | -------- | ---------------- | ------------------------------------------------------------------------------------------------------- |
| `SLACK_BOT_TOKEN`  | yes      | —                | Slack bot token (`xoxb-...`)                                                                            |
| `SLACK_APP_TOKEN`  | yes      | —                | Slack app-level token for Socket Mode (`xapp-...`)                                                      |
| `PROVIDER`         | no       | `openai`         | LLM provider: `openai`, `anthropic`, or `google`                                                        |
| provider API key   | yes      | —                | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GEMINI_API_KEY` depending on `PROVIDER`                       |
| `MODEL`            | no       | per provider     | `gpt-4o-mini` (openai), `claude-sonnet-4-6` (anthropic), `gemini-2.5-flash` (google); startup fails on unknown models |
| `AGENT_TIMEOUT_MS` | no       | `180000`         | Abort an agent run after this many milliseconds                                                         |
| `GITHUB_TOKEN`     | yes      | —                | Token with Issues:write on `GITHUB_REPO`                                                                |
| `GITHUB_REPO`      | yes      | —                | Target repository as `owner/name`                                                                       |
| `DRY_RUN`          | no       | `true`           | Global safety override: approved drafts produce would-be payloads only. Only `false`/`0`/`no` disables. |

## Architecture

- `src/slack.ts` — Bolt app in Socket Mode. Draft → approve → file flow with an in-memory pending-draft store, per-conversation run serialization, event-id dedup, and structured JSON run logs. Formatting and state helpers are pure, Bolt-free, and unit-tested.
- `src/agent.ts` — the pi agent only *drafts* (sole tool: terminal `submit_prd`); filing lives in deterministic `fileIssues(prd, github, dryRun)`. LLM proposes, code disposes.
- `src/github.ts` — a minimal GitHub issues client built on plain `fetch` (no octokit).

## Development

```sh
bun test             # unit tests (no network or API keys needed)
bun run typecheck    # tsc --noEmit
bun run lint         # biome check .
```

## License

MIT — see [LICENSE](LICENSE).
