# Agent PM

A Slack-native bot that turns product ideas into PRDs and tickets. Post an idea (app mention or DM) → a [pi](https://github.com/earendil-works/pi) agent drafts a structured PRD → one GitHub issue is filed per user story → the bot replies in the Slack thread with the PRD and issue links.

This is a full rewrite of the previous Python/FastAPI service, which is preserved on the [`archive/python-legacy`](https://github.com/evalops/agent-pm/tree/archive/python-legacy) branch.

## Quick start

1. **Create the Slack app** (at [api.slack.com/apps](https://api.slack.com/apps)):
   - Enable **Socket Mode** and create an app-level token with the `connections:write` scope → `SLACK_APP_TOKEN` (`xapp-...`).
   - Under **OAuth & Permissions**, add bot scopes: `app_mentions:read`, `chat:write`, `im:history`, `im:read`, `im:write`. Install the app to your workspace → `SLACK_BOT_TOKEN` (`xoxb-...`).
   - Under **Event Subscriptions**, subscribe to the bot events `app_mention` and `message.im`.
2. **GitHub token**: a token (fine-grained PAT or GitHub App token) with **Issues: write** on the target repository.
3. **Configure and run**:

   ```sh
   bun install
   cp .env.example .env   # fill in the values
   bun run dev            # or: bun start
   ```

4. Mention the bot in a channel (`@Agent PM an idea for...`) or DM it directly.

## Configuration

| Variable          | Required | Default       | Description                                                   |
| ----------------- | -------- | ------------- | ------------------------------------------------------------- |
| `SLACK_BOT_TOKEN` | yes      | —             | Slack bot token (`xoxb-...`)                                  |
| `SLACK_APP_TOKEN` | yes      | —             | Slack app-level token for Socket Mode (`xapp-...`)            |
| `OPENAI_API_KEY`  | yes      | —             | OpenAI key used by the pi agent                               |
| `GITHUB_TOKEN`    | yes      | —             | Token with Issues:write on `GITHUB_REPO`                      |
| `GITHUB_REPO`     | yes      | —             | Target repository as `owner/name`                             |
| `DRY_RUN`         | no       | `true`        | When true, no issues are created; the reply shows the payloads that would be filed. Only `false`/`0`/`no` disables it. |
| `MODEL`           | no       | `gpt-4o-mini` | OpenAI model used by the agent                                |

## Dry run

`DRY_RUN` defaults to `true`, carried over from the old service's safety discipline: the agent still plans one issue per user story, but nothing is written to GitHub. The Slack reply shows each issue payload that *would* be filed, so you can review the output end-to-end before flipping `DRY_RUN=false`.

## Architecture

- `src/slack.ts` — Bolt app in Socket Mode; mention/DM handlers post a "drafting…" placeholder, run the agent, and update the thread. Handler logic lives in the pure `handleIdea(text, deps)` function, separate from the Bolt wiring.
- `src/agent.ts` — one fresh pi `Agent` per request with two tools: `create_github_issue` (real or dry-run) and a terminal `submit_prd` tool that captures the structured PRD.
- `src/github.ts` — a minimal GitHub issues client built on plain `fetch` (no octokit).

## Development

```sh
bun test             # unit tests (no network or API keys needed)
bun run typecheck    # tsc --noEmit
bun run lint         # biome check .
```

## License

MIT — see [LICENSE](LICENSE).
