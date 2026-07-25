# AGENTS.md

Guidance for AI agents (and humans) working in this repo.

## What this is

Agent PM — a Slack-native bot that turns product ideas into PRDs and GitHub issues, built on the [pi](https://github.com/earendil-works/pi) agent harness (TypeScript, Bun runtime, Socket Mode Slack via `@slack/bolt`).

## pi conventions

- Import `Type` (TypeBox) from `@earendil-works/pi-ai`. Never add `typebox` as a direct dependency.
- LLM call failures do **not** throw. After `agent.prompt()` resolves, check `agent.state.errorMessage` (and whether the terminal tool ran) instead of wrapping the call in try/catch.
- One active run per `Agent` instance — create a fresh `Agent` per request and share the `models` registry (`createModels()`).
- Use the terminal-tool pattern for structured output: a `submit_*` tool whose `execute` captures params and returns `terminate: true`.
- Pin all `@earendil-works/*` packages to the same exact version, and read the changelog before bumping — the packages move in lockstep.
- Tests use `fauxProvider()` from pi-ai. Never require network or API keys in tests.

## Safety discipline

- Any mutation (filing GitHub issues) is gated behind **both** an explicit human approval (✅ reaction in Slack) **and** `DRY_RUN=false`. Keep it that way.
- The LLM proposes, code disposes: the agent only drafts; side effects live in deterministic, unit-tested code (`fileIssues`).

## Architecture rule

No subsystem without a producer and a consumer on day one. No queues, plugin platforms, storage layers, or dashboards "for later". In-memory state (pending drafts, event dedup) is deliberate — a restart loses it, and that's acceptable.
