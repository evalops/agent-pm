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
		expect(config.model).toBe("gpt-4o");
	});

	test("throws listing all missing variables", () => {
		expect(() => loadConfig({})).toThrow(
			"SLACK_BOT_TOKEN, SLACK_APP_TOKEN, OPENAI_API_KEY, GITHUB_TOKEN, GITHUB_REPO",
		);
	});

	test("rejects a malformed GITHUB_REPO", () => {
		expect(() => loadConfig({ ...VALID_ENV, GITHUB_REPO: "no-slash" })).toThrow("owner/name");
	});

	test("DRY_RUN defaults to true when unset", () => {
		expect(loadConfig(VALID_ENV).dryRun).toBe(true);
	});

	test.each(["true", "yes", "1", "anything"])("DRY_RUN=%p keeps dry-run on", (value) => {
		expect(loadConfig({ ...VALID_ENV, DRY_RUN: value }).dryRun).toBe(true);
	});

	test.each(["false", "0", "no"])("DRY_RUN=%p disables dry-run", (value) => {
		expect(loadConfig({ ...VALID_ENV, DRY_RUN: value }).dryRun).toBe(false);
	});

	test("MODEL defaults to gpt-4o-mini", () => {
		expect(loadConfig(VALID_ENV).model).toBe("gpt-4o-mini");
	});
});
