import { describe, expect, test } from "bun:test";
import {
	type Context,
	contentText,
	createModels,
	fauxAssistantMessage,
	fauxProvider,
	fauxToolCall,
} from "@earendil-works/pi-ai";
import { fileIssues, formatPrompt, runPrdAgent, storyBody } from "../src/agent.ts";
import type { GitHubClient } from "../src/github.ts";

const PRD = {
	title: "Idea inbox",
	summary: "A place to collect product ideas.",
	problem: "Ideas get lost in chat.",
	goals: ["Capture ideas", "Draft PRDs"],
	nonGoals: ["Roadmapping"],
	userStories: [
		{
			title: "As a PM I want to post an idea so that a PRD is drafted",
			description: "Post an idea in Slack, get a PRD back.",
			acceptanceCriteria: ["PRD returned in thread", "Stories filed as issues"],
		},
		{
			title: "As a PM I want to approve a draft so that issues are filed",
			description: "React with a checkmark to file.",
			acceptanceCriteria: ["One issue per story"],
		},
	],
};

function setup() {
	const faux = fauxProvider();
	const models = createModels();
	models.setProvider(faux.provider);
	return { faux, models, model: faux.getModel() };
}

const noGitHub: GitHubClient = {
	createIssue: () => {
		throw new Error("github should not be called in dry-run");
	},
};

describe("runPrdAgent", () => {
	test("captures the PRD submitted via the terminal tool", async () => {
		const { faux, models, model } = setup();
		faux.setResponses([fauxAssistantMessage(fauxToolCall("submit_prd", PRD))]);

		const prd = await runPrdAgent("an idea inbox", { models, model, timeoutMs: 5000 });

		expect(prd.title).toBe("Idea inbox");
		expect(prd.userStories).toHaveLength(2);
	});

	test("prepends history to the prompt", async () => {
		const { faux, models, model } = setup();
		let seenPrompt = "";
		faux.setResponses([
			(context: Context) => {
				const last = context.messages.at(-1);
				seenPrompt = last?.role === "user" ? contentText(last.content) : "";
				return fauxAssistantMessage(fauxToolCall("submit_prd", PRD));
			},
		]);

		await runPrdAgent("make the non-goals stricter", { models, model, timeoutMs: 5000 }, [
			{ role: "user", text: "an idea inbox" },
			{ role: "assistant", text: "PRD draft: Idea inbox" },
		]);

		expect(seenPrompt).toContain("Conversation so far:");
		expect(seenPrompt).toContain("User: an idea inbox");
		expect(seenPrompt).toContain("Assistant: PRD draft: Idea inbox");
		expect(seenPrompt).toContain("Latest request: make the non-goals stricter");
	});

	test("times out a slow run via abort", async () => {
		const { faux, models, model } = setup();
		faux.setResponses([
			async () => {
				await new Promise((resolve) => setTimeout(resolve, 300));
				return fauxAssistantMessage(fauxToolCall("submit_prd", PRD));
			},
		]);

		await expect(runPrdAgent("x", { models, model, timeoutMs: 50 })).rejects.toThrow("timed out");
	});

	test("throws with the agent error message when the model errors", async () => {
		const { faux, models, model } = setup();
		faux.setResponses([
			fauxAssistantMessage("boom", { stopReason: "error", errorMessage: "provider exploded" }),
		]);

		await expect(runPrdAgent("x", { models, model, timeoutMs: 5000 })).rejects.toThrow(
			"provider exploded",
		);
	});

	test("throws with the last assistant text when submit_prd is never called", async () => {
		const { faux, models, model } = setup();
		faux.setResponses([fauxAssistantMessage("here is some prose instead of a tool call")]);

		await expect(runPrdAgent("x", { models, model, timeoutMs: 5000 })).rejects.toThrow(
			"here is some prose instead of a tool call",
		);
	});
});

describe("formatPrompt", () => {
	test("returns the idea unchanged without history", () => {
		expect(formatPrompt("an idea")).toBe("an idea");
		expect(formatPrompt("an idea", [])).toBe("an idea");
	});
});

describe("storyBody", () => {
	const STORY0 = PRD.userStories[0] as (typeof PRD.userStories)[number];

	test("renders description plus acceptance criteria checklist", () => {
		expect(storyBody(STORY0)).toBe(
			"Post an idea in Slack, get a PRD back.\n\n## Acceptance criteria\n- [ ] PRD returned in thread\n- [ ] Stories filed as issues",
		);
	});
});

describe("fileIssues", () => {
	const STORY0 = PRD.userStories[0] as (typeof PRD.userStories)[number];
	const STORY1 = PRD.userStories[1] as (typeof PRD.userStories)[number];

	test("dry-run records would-be payloads without calling GitHub", async () => {
		const result = await fileIssues(PRD, noGitHub, true);
		expect(result.failed).toEqual([]);
		expect(result.filed).toHaveLength(2);
		expect(result.filed[0]?.dryRun).toBe(true);
		expect(result.filed[0]?.body).toContain("- [ ] PRD returned in thread");
	});

	test("files one issue per story and keeps going past failures", async () => {
		const calls: string[] = [];
		const github: GitHubClient = {
			createIssue: async (title) => {
				calls.push(title);
				if (calls.length === 1) throw new Error("422 Validation Failed");
				return { number: 9, url: "https://github.com/o/r/issues/9" };
			},
		};

		const result = await fileIssues(PRD, github, false);

		expect(calls).toHaveLength(2);
		expect(result.filed).toEqual([
			{
				title: STORY1.title,
				body: storyBody(STORY1),
				number: 9,
				url: "https://github.com/o/r/issues/9",
				dryRun: false,
			},
		]);
		expect(result.failed).toEqual([{ title: STORY0.title, error: "422 Validation Failed" }]);
	});
});
