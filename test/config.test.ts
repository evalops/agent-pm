import { describe, expect, test } from "bun:test";
import { loadConfig } from "../src/config.ts";

const VALID_ENV = {
	SLACK_BOT_TOKEN: "xoxb-test",
	SLACK_APP_TOKEN: "xapp-test",
	OPENAI_API_KEY: "sk-test",
	GITHUB_TOKEN: "ghp-test",
	GITHUB_REPO: "evalops/agent-pm",
};

describe("loadConfig", () => {
	test("parses a full environment", () => {
		const config = loadConfig({ ...VALID_ENV, MODEL: "gpt-4o" });
		expect(config.slackBotToken).toBe("xoxb-test");
		expect(config.githubRepo).toBe("evalops/agent-pm");
		expect(config.provider).toBe("openai");
		expect(config.model).toBe("gpt-4o");
	});

	test("throws listing all missing variables", () => {
		expect(() => loadConfig({})).toThrow(
			"SLACK_BOT_TOKEN, SLACK_APP_TOKEN, GITHUB_TOKEN, GITHUB_REPO",
		);
	});

	test("rejects a malformed GITHUB_REPO", () => {
		expect(() => loadConfig({ ...VALID_ENV, GITHUB_REPO: "no-slash" })).toThrow("owner/name");
	});

	test("DRY_RUN defaults to true when unset", () => {
		expect(loadConfig(VALID_ENV).dryRun).toBe(true);
	});

	test.each(["true", "yes", "1", "anything"])("DRY_RUN=%p keeps dry-run on", (value: string) => {
		expect(loadConfig({ ...VALID_ENV, DRY_RUN: value }).dryRun).toBe(true);
	});

	test.each(["false", "0", "no"])("DRY_RUN=%p disables dry-run", (value: string) => {
		expect(loadConfig({ ...VALID_ENV, DRY_RUN: value }).dryRun).toBe(false);
	});

	test("MODEL defaults to gpt-4o-mini for openai", () => {
		expect(loadConfig(VALID_ENV).model).toBe("gpt-4o-mini");
	});

	test("PROVIDER defaults to openai and requires OPENAI_API_KEY", () => {
		const { OPENAI_API_KEY, ...rest } = VALID_ENV;
		expect(() => loadConfig(rest)).toThrow('provider "openai": OPENAI_API_KEY');
	});

	test("rejects an unknown PROVIDER", () => {
		expect(() => loadConfig({ ...VALID_ENV, PROVIDER: "mistral" })).toThrow(
			"PROVIDER must be one of openai, anthropic, google",
		);
	});

	test("anthropic provider needs ANTHROPIC_API_KEY and defaults to claude-sonnet-4-6", () => {
		const { OPENAI_API_KEY, ...rest } = VALID_ENV;
		const config = loadConfig({ ...rest, PROVIDER: "anthropic", ANTHROPIC_API_KEY: "sk-ant" });
		expect(config.provider).toBe("anthropic");
		expect(config.model).toBe("claude-sonnet-4-6");
	});

	test("google provider needs GEMINI_API_KEY and defaults to gemini-2.5-flash", () => {
		const { OPENAI_API_KEY, ...rest } = VALID_ENV;
		const config = loadConfig({ ...rest, PROVIDER: "google", GEMINI_API_KEY: "gm-key" });
		expect(config.provider).toBe("google");
		expect(config.model).toBe("gemini-2.5-flash");
	});

	test("AGENT_TIMEOUT_MS defaults to 180000", () => {
		expect(loadConfig(VALID_ENV).agentTimeoutMs).toBe(180000);
	});

	test("AGENT_TIMEOUT_MS parses a positive integer", () => {
		expect(loadConfig({ ...VALID_ENV, AGENT_TIMEOUT_MS: "5000" }).agentTimeoutMs).toBe(5000);
	});

	test.each(["0", "-5", "abc"])("AGENT_TIMEOUT_MS=%p is rejected", (value: string) => {
		expect(() => loadConfig({ ...VALID_ENV, AGENT_TIMEOUT_MS: value })).toThrow(
			"AGENT_TIMEOUT_MS must be a positive integer",
		);
	});
});
