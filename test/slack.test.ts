import { describe, expect, test } from "bun:test";
import type { Prd } from "../src/agent.ts";
import {
	DraftStore,
	formatDraft,
	formatFiled,
	makeConversationQueue,
	makeEventDedup,
	type PendingDraft,
	truncate,
} from "../src/slack.ts";

const PRD: Prd = {
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
		{
			title: "As a PM I want to approve a draft",
			description: "React to file issues.",
			acceptanceCriteria: ["One issue per story"],
		},
	],
};

describe("truncate", () => {
	test("leaves short text alone", () => {
		expect(truncate("hello")).toBe("hello");
	});

	test("caps long text with a marker", () => {
		const out = truncate("x".repeat(5000));
		expect(out.length).toBeLessThanOrEqual(3920);
		expect(out.endsWith("…_(truncated)_")).toBe(true);
	});
});

describe("formatDraft", () => {
	test("renders the PRD with an approval instruction and story count", () => {
		const reply = formatDraft(PRD);
		expect(reply).toContain("*PRD draft: Idea inbox*");
		expect(reply).toContain("*Goals*\n• Capture ideas");
		expect(reply).toContain("1. *As a PM I want to post an idea*");
		expect(reply).toContain(":white_check_mark: to file 2 issue(s)");
	});

	test("truncates oversized PRDs", () => {
		const huge = { ...PRD, summary: "s".repeat(5000) };
		expect(formatDraft(huge).endsWith("…_(truncated)_")).toBe(true);
	});
});

describe("formatFiled", () => {
	test("renders filed issue links with a count", () => {
		const reply = formatFiled(
			{
				filed: [
					{
						title: "Story",
						body: "Body",
						number: 7,
						url: "https://github.com/o/r/issues/7",
						dryRun: false,
					},
				],
				failed: [],
			},
			2,
			false,
		);
		expect(reply).toContain("Filed 1 of 2 issue(s):");
		expect(reply).toContain("<https://github.com/o/r/issues/7|#7> — Story");
	});

	test("renders dry-run payloads", () => {
		const reply = formatFiled(
			{ filed: [{ title: "Story", body: "Would-be body", dryRun: true }], failed: [] },
			1,
			true,
		);
		expect(reply).toContain("*DRY RUN* — would file 1 of 1 issue(s):");
		expect(reply).toContain("Would-be body");
	});

	test("lists per-story failures with their error messages", () => {
		const reply = formatFiled(
			{ filed: [], failed: [{ title: "Broken story", error: "403 Forbidden" }] },
			1,
			false,
		);
		expect(reply).toContain("Filed 0 of 1 issue(s):");
		expect(reply).toContain(":x: Broken story — 403 Forbidden");
	});
});

describe("makeEventDedup", () => {
	test("accepts new ids and drops duplicates", () => {
		const dedup = makeEventDedup();
		expect(dedup.isNew("Ev1")).toBe(true);
		expect(dedup.isNew("Ev1")).toBe(false);
		expect(dedup.isNew("Ev2")).toBe(true);
	});

	test("evicts the oldest id once over capacity", () => {
		const dedup = makeEventDedup(3);
		for (const id of ["a", "b", "c", "d"]) expect(dedup.isNew(id)).toBe(true);
		// "a" was evicted when "d" arrived; "b" is still cached.
		expect(dedup.isNew("b")).toBe(false);
		expect(dedup.isNew("a")).toBe(true);
	});
});

describe("makeConversationQueue", () => {
	test("serializes work for the same key", async () => {
		const queue = makeConversationQueue();
		const order: string[] = [];
		let releaseFirst: () => void = () => {};
		const firstDone = new Promise<void>((resolve) => {
			releaseFirst = resolve;
		});

		const first = queue.enqueue("k", async () => {
			await firstDone;
			order.push("first");
		});
		const second = queue.enqueue("k", async () => {
			order.push("second");
		});

		await new Promise((resolve) => setTimeout(resolve, 10));
		expect(order).toEqual([]); // second is still blocked behind first
		releaseFirst();
		await Promise.all([first, second]);
		expect(order).toEqual(["first", "second"]);
	});

	test("continues the chain after a failure", async () => {
		const queue = makeConversationQueue();
		await expect(
			queue.enqueue("k", async () => {
				throw new Error("boom");
			}),
		).rejects.toThrow("boom");
		await expect(queue.enqueue("k", async () => "recovered")).resolves.toBe("recovered");
	});
});

describe("DraftStore", () => {
	const draft = (messageTs: string): PendingDraft => ({
		convKey: "C1:111.0",
		channel: "C1",
		replyThreadTs: "111.0",
		messageTs,
		prd: PRD,
		state: "pending",
	});

	test("tracks the current draft per conversation", () => {
		const store = new DraftStore();
		const d = draft("222.0");
		store.set(d);
		expect(store.getCurrent("C1:111.0")).toBe(d);
		expect(store.getByMessageTs("222.0")).toBe(d);
	});

	test("a new draft supersedes the pending one", () => {
		const store = new DraftStore();
		const oldDraft = draft("222.0");
		const newDraft = draft("333.0");
		store.set(oldDraft);
		store.set(newDraft);
		expect(oldDraft.state).toBe("superseded");
		expect(store.getCurrent("C1:111.0")).toBe(newDraft);
		expect(store.getByMessageTs("222.0")?.state).toBe("superseded");
	});

	test("filed drafts are not resurrected as pending", () => {
		const store = new DraftStore();
		const d = draft("222.0");
		store.set(d);
		d.state = "filed";
		store.set(draft("333.0"));
		expect(store.getCurrent("C1:111.0")?.messageTs).toBe("333.0");
		expect(store.getByMessageTs("222.0")?.state).toBe("filed");
	});
});
