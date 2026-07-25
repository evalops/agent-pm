import { App } from "@slack/bolt";
import { type FileIssuesResult, fileIssues, type HistoryEntry, type Prd } from "./agent.ts";
import type { Config } from "./config.ts";
import type { GitHubClient } from "./github.ts";

export interface SlackDeps {
	runAgent: (idea: string, history?: HistoryEntry[]) => Promise<Prd>;
	github: GitHubClient;
	dryRun: boolean;
}

// ---------------------------------------------------------------------------
// Pure helpers (unit-tested without Bolt)

/** Slack's text limit is ~4000 chars; stay well under it. */
const MAX_TEXT = 3900;

export function truncate(text: string): string {
	if (text.length <= MAX_TEXT) return text;
	return `${text.slice(0, MAX_TEXT)}\n…_(truncated)_`;
}

function bullet(items: string[]): string {
	return items.map((i) => `• ${i}`).join("\n");
}

/** Render a drafted PRD with the approval instruction. */
export function formatDraft(prd: Prd): string {
	const stories = prd.userStories
		.map((s, i) => `${i + 1}. *${s.title}*\n   ${s.description}`)
		.join("\n");
	return truncate(
		[
			`*PRD draft: ${prd.title}*`,
			"",
			`*Summary*\n${prd.summary}`,
			"",
			`*Problem*\n${prd.problem}`,
			"",
			`*Goals*\n${bullet(prd.goals)}`,
			"",
			`*Non-goals*\n${bullet(prd.nonGoals)}`,
			"",
			`*User stories*\n${stories}`,
			"",
			`_React with :white_check_mark: to file ${prd.userStories.length} issue(s), or reply in this thread to revise the draft._`,
		].join("\n"),
	);
}

/** Render the outcome of filing issues for an approved draft. */
export function formatFiled(result: FileIssuesResult, storyCount: number, dryRun: boolean): string {
	const lines: string[] = [];
	if (dryRun) {
		lines.push(`*DRY RUN* — would file ${result.filed.length} of ${storyCount} issue(s):`);
		for (const issue of result.filed) {
			lines.push(`• *${issue.title}*\n\`\`\`\n${issue.body}\n\`\`\``);
		}
	} else {
		lines.push(`Filed ${result.filed.length} of ${storyCount} issue(s):`);
		for (const issue of result.filed) {
			lines.push(`• <${issue.url}|#${issue.number}> — ${issue.title}`);
		}
	}
	for (const failure of result.failed) {
		lines.push(`• :x: ${failure.title} — ${failure.error}`);
	}
	return truncate(lines.join("\n"));
}

/** In-memory FIFO dedup for Slack event ids. */
export function makeEventDedup(limit = 1000) {
	const seen = new Set<string>();
	return {
		/** Returns true the first time an id is seen, false for duplicates. */
		isNew(id: string): boolean {
			if (seen.has(id)) return false;
			if (seen.size >= limit) {
				const oldest = seen.values().next().value;
				if (oldest !== undefined) seen.delete(oldest);
			}
			seen.add(id);
			return true;
		},
	};
}

/** Serialize async work per key; different keys run in parallel. */
export function makeConversationQueue() {
	const chains = new Map<string, Promise<unknown>>();
	return {
		enqueue<T>(key: string, fn: () => Promise<T>): Promise<T> {
			const next = (chains.get(key) ?? Promise.resolve()).then(fn);
			chains.set(
				key,
				next.catch(() => {}),
			);
			return next;
		},
	};
}

export interface PendingDraft {
	/** Conversation key: `${channel}:${threadTs}`. */
	convKey: string;
	channel: string;
	/** Thread ts to post replies into (the draft message itself for flat DMs). */
	replyThreadTs: string;
	/** ts of the message holding this draft (what users react to). */
	messageTs: string;
	prd: Prd;
	state: "pending" | "superseded" | "filed";
}

/** Tracks the latest draft per conversation; older drafts stay findable so stale approvals can be rejected. */
export class DraftStore {
	private byConv = new Map<string, PendingDraft>();
	private byTs = new Map<string, PendingDraft>();

	set(draft: PendingDraft): void {
		const previous = this.byConv.get(draft.convKey);
		if (previous && previous.state === "pending") previous.state = "superseded";
		this.byConv.set(draft.convKey, draft);
		this.byTs.set(draft.messageTs, draft);
	}

	getCurrent(convKey: string): PendingDraft | undefined {
		return this.byConv.get(convKey);
	}

	getByMessageTs(messageTs: string): PendingDraft | undefined {
		return this.byTs.get(messageTs);
	}
}

export type RunOutcome = "drafted" | "filed" | "failed";

/** One structured JSON log line per completed run. */
export function logRun(entry: {
	conversation: string;
	durationMs: number;
	storyCount?: number;
	dryRun: boolean;
	outcome: RunOutcome;
	error?: string;
}): void {
	console.log(JSON.stringify({ ts: new Date().toISOString(), ...entry }));
}

const APPROVAL_EMOJI = "white_check_mark";

// ---------------------------------------------------------------------------
// Bolt wiring

/** Bolt app in Socket Mode: draft on mention/DM, file on ✅ approval. */
export function createSlackApp(config: Config, deps: SlackDeps): App {
	const app = new App({
		token: config.slackBotToken,
		appToken: config.slackAppToken,
		socketMode: true,
	});

	const dedup = makeEventDedup();
	const queue = makeConversationQueue();
	const drafts = new DraftStore();

	const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

	const runDraft = async (
		channel: string,
		convKey: string,
		threadTs: string | undefined,
		idea: string,
	) => {
		const started = Date.now();
		const placeholder = await app.client.chat.postMessage({
			channel,
			...(threadTs ? { thread_ts: threadTs } : {}),
			text: ":hourglass_flowing_sand: Drafting a PRD…",
		});
		const placeholderTs = placeholder.ts;

		try {
			const history = await fetchHistory(channel, threadTs);
			const prd = await deps.runAgent(idea, history);
			if (!placeholderTs) throw new Error("placeholder message returned no ts");
			await app.client.chat.update({ channel, ts: placeholderTs, text: formatDraft(prd) });
			await app.client.reactions.add({ channel, timestamp: placeholderTs, name: APPROVAL_EMOJI });
			drafts.set({
				convKey,
				channel,
				replyThreadTs: threadTs ?? placeholderTs,
				messageTs: placeholderTs,
				prd,
				state: "pending",
			});
			logRun({
				conversation: convKey,
				durationMs: Date.now() - started,
				storyCount: prd.userStories.length,
				dryRun: deps.dryRun,
				outcome: "drafted",
			});
		} catch (err) {
			if (placeholderTs) {
				await app.client.chat.update({
					channel,
					ts: placeholderTs,
					text: `:warning: Couldn't draft a PRD: ${errorText(err)}`,
				});
			}
			logRun({
				conversation: convKey,
				durationMs: Date.now() - started,
				dryRun: deps.dryRun,
				outcome: "failed",
				error: errorText(err),
			});
		}
	};

	/** Thread history for follow-ups: replies in a thread, or recent DM history for flat DMs. */
	const fetchHistory = async (
		channel: string,
		threadTs: string | undefined,
	): Promise<HistoryEntry[]> => {
		const isFlatDm = threadTs === undefined;
		const res = isFlatDm
			? await app.client.conversations.history({ channel, limit: 20 })
			: await app.client.conversations.replies({ channel, ts: threadTs ?? "", limit: 20 });
		const messages = res.messages ?? [];
		// conversations.history is newest-first; normalize to chronological.
		if (isFlatDm) messages.reverse();
		return messages
			.filter((m) => m.ts && m.text)
			.map((m) => ({
				role: (m.bot_id ? "assistant" : "user") as HistoryEntry["role"],
				text: (m.text ?? "").slice(0, 500),
			}));
	};

	const fileApproved = async (draft: PendingDraft) => {
		const started = Date.now();
		try {
			const result = await fileIssues(draft.prd, deps.github, deps.dryRun);
			draft.state = "filed";
			await app.client.chat.postMessage({
				channel: draft.channel,
				thread_ts: draft.replyThreadTs,
				text: formatFiled(result, draft.prd.userStories.length, deps.dryRun),
			});
			logRun({
				conversation: draft.convKey,
				durationMs: Date.now() - started,
				storyCount: result.filed.length,
				dryRun: deps.dryRun,
				outcome: result.failed.length > 0 ? "failed" : "filed",
				...(result.failed.length > 0
					? {
							error: `${result.failed.length} issue(s) failed: ${result.failed.map((f) => f.title).join(", ")}`,
						}
					: {}),
			});
		} catch (err) {
			await app.client.chat.postMessage({
				channel: draft.channel,
				thread_ts: draft.replyThreadTs,
				text: `:warning: Couldn't file issues: ${errorText(err)}`,
			});
			logRun({
				conversation: draft.convKey,
				durationMs: Date.now() - started,
				dryRun: deps.dryRun,
				outcome: "failed",
				error: errorText(err),
			});
		}
	};

	app.event("app_mention", async ({ event, body }) => {
		if (!dedup.isNew(body.event_id)) return;
		const idea = event.text.replace(/<@[A-Z0-9]+>/g, "").trim();
		if (!idea) return;
		const threadTs = event.thread_ts ?? event.ts;
		const convKey = `${event.channel}:${threadTs}`;
		await queue.enqueue(convKey, () => runDraft(event.channel, convKey, threadTs, idea));
	});

	app.message(async ({ event, body }) => {
		if (event.subtype !== undefined) return;
		if (event.channel_type !== "im") return;
		if (!dedup.isNew(body.event_id)) return;
		const idea = event.text?.trim();
		if (!idea) return;
		// Flat DMs are one ongoing conversation per channel; threaded DM replies get their own.
		const threadTs = event.thread_ts;
		const convKey = `${event.channel}:${threadTs ?? "dm"}`;
		await queue.enqueue(convKey, () => runDraft(event.channel, convKey, threadTs, idea));
	});

	app.event("reaction_added", async ({ event, body, context }) => {
		if (!dedup.isNew(body.event_id)) return;
		if (event.reaction !== APPROVAL_EMOJI) return;
		if (event.item.type !== "message") return;
		// Ignore the bot's own seeding reaction and any bot user's reaction.
		if ((event as { bot_id?: string }).bot_id || event.user === context.botUserId) return;

		const draft = drafts.getByMessageTs(event.item.ts);
		if (!draft) return;
		if (draft.state === "superseded") {
			await app.client.chat.postMessage({
				channel: draft.channel,
				thread_ts: draft.replyThreadTs,
				text: ":warning: This draft was superseded by a newer one — approve the latest draft instead.",
			});
			return;
		}
		if (draft.state !== "pending") return;
		// Only the current draft of a conversation is approve-able.
		if (drafts.getCurrent(draft.convKey) !== draft) return;

		await queue.enqueue(draft.convKey, () => fileApproved(draft));
	});

	return app;
}
