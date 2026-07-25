import { describe, expect, test } from "bun:test";
import {
	createModels,
	fauxAssistantMessage,
	fauxProvider,
	fauxToolCall,
} from "@earendil-works/pi-ai";
import { runPrdAgent } from "../src/agent.ts";
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
	test("captures the PRD and records dry-run issue payloads without calling GitHub", async () => {
		const { faux, models, model } = setup();
		faux.setResponses([
			fauxAssistantMessage(
				fauxToolCall("create_github_issue", { title: "Story: post idea", body: "Body text" }),
			),
			fauxAssistantMessage(fauxToolCall("submit_prd", PRD)),
		]);

		const result = await runPrdAgent("an idea inbox", {
			models,
			model,
			github: noGitHub,
			dryRun: true,
		});

		expect(result.prd.title).toBe("Idea inbox");
		expect(result.prd.userStories).toHaveLength(1);
		expect(result.issues).toEqual([{ title: "Story: post idea", body: "Body text", dryRun: true }]);
	});

	test("creates real issues when dry-run is off", async () => {
		const { faux, models, model } = setup();
		faux.setResponses([
			fauxAssistantMessage(fauxToolCall("create_github_issue", { title: "Story", body: "Body" })),
			fauxAssistantMessage(fauxToolCall("submit_prd", PRD)),
		]);
		const created: string[] = [];
		const github: GitHubClient = {
			createIssue: async (title) => {
				created.push(title);
				return { number: 7, url: "https://github.com/o/r/issues/7" };
			},
		};

		const result = await runPrdAgent("an idea inbox", { models, model, github, dryRun: false });

		expect(created).toEqual(["Story"]);
		expect(result.issues).toEqual([
			{
				title: "Story",
				body: "Body",
				number: 7,
				url: "https://github.com/o/r/issues/7",
				dryRun: false,
			},
		]);
	});

	test("throws with the agent error message when the model errors", async () => {
		const { faux, models, model } = setup();
		faux.setResponses([
			fauxAssistantMessage("boom", { stopReason: "error", errorMessage: "provider exploded" }),
		]);

		await expect(
			runPrdAgent("x", { models, model, github: noGitHub, dryRun: true }),
		).rejects.toThrow("provider exploded");
	});

	test("throws with the last assistant text when submit_prd is never called", async () => {
		const { faux, models, model } = setup();
		faux.setResponses([fauxAssistantMessage("here is some prose instead of a tool call")]);

		await expect(
			runPrdAgent("x", { models, model, github: noGitHub, dryRun: true }),
		).rejects.toThrow("here is some prose instead of a tool call");
	});
});
