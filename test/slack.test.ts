import { describe, expect, test } from "bun:test";
import type { PrdRunResult } from "../src/agent.ts";
import { formatReply, handleIdea } from "../src/slack.ts";

const RESULT: PrdRunResult = {
	prd: {
		title: "Idea inbox",
		summary: "Collect ideas.",
		problem: "Ideas get lost.",
		goals: ["Capture ideas"],
		nonGoals: ["Roadmapping"],
		userStories: [
			{
				title: "As a PM I want to post an idea",
				description: "Post in Slack, get a PRD.",
				acceptanceCriteria: ["PRD in thread"],
			},
		],
	},
	issues: [],
};

describe("formatReply", () => {
	test("renders the PRD and real issue links", () => {
		const reply = formatReply({
			...RESULT,
			issues: [
				{
					title: "Story",
					body: "Body",
					number: 7,
					url: "https://github.com/o/r/issues/7",
					dryRun: false,
				},
			],
		});
		expect(reply).toContain("*PRD: Idea inbox*");
		expect(reply).toContain("*Goals*\n• Capture ideas");
		expect(reply).toContain("<https://github.com/o/r/issues/7|#7> — Story");
	});

	test("renders dry-run issue payloads", () => {
		const reply = formatReply({
			...RESULT,
			issues: [{ title: "Story", body: "Would-be body", dryRun: true }],
		});
		expect(reply).toContain("_dry run — would file:_ *Story*");
		expect(reply).toContain("Would-be body");
	});

	test("notes when no issues were filed", () => {
		expect(formatReply(RESULT)).toContain("_No issues filed._");
	});
});

describe("handleIdea", () => {
	test("returns the formatted reply from the agent result", async () => {
		const reply = await handleIdea("an idea", { runAgent: async () => RESULT });
		expect(reply).toContain("*PRD: Idea inbox*");
	});

	test("propagates agent errors", async () => {
		await expect(
			handleIdea("an idea", {
				runAgent: async () => {
					throw new Error("agent blew up");
				},
			}),
		).rejects.toThrow("agent blew up");
	});
});
