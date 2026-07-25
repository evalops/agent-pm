import { App } from "@slack/bolt";
import type { PrdRunResult } from "./agent.ts";
import type { Config } from "./config.ts";

export interface IdeaDeps {
	runAgent: (idea: string) => Promise<PrdRunResult>;
}

/** Render the agent result as Slack mrkdwn. */
export function formatReply(result: PrdRunResult): string {
	const { prd, issues } = result;
	const bullet = (items: string[]) => items.map((i) => `• ${i}`).join("\n");
	const stories = prd.userStories
		.map((s, i) => `${i + 1}. *${s.title}*\n   ${s.description}`)
		.join("\n");

	const issueLines =
		issues.length === 0
			? "_No issues filed._"
			: issues
					.map((issue) => {
						if (issue.dryRun) {
							return `• _dry run — would file:_ *${issue.title}*\n\`\`\`\n${issue.body}\n\`\`\``;
						}
						return `• <${issue.url}|#${issue.number}> — ${issue.title}`;
					})
					.join("\n");

	return [
		`*PRD: ${prd.title}*`,
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
		`*Issues*\n${issueLines}`,
	].join("\n");
}

/**
 * Pure handler: run the PRD agent for one idea and return the reply text.
 * Throws on agent failure; callers decide how to surface it.
 */
export async function handleIdea(text: string, deps: IdeaDeps): Promise<string> {
	const result = await deps.runAgent(text);
	return formatReply(result);
}

function errorText(err: unknown): string {
	return err instanceof Error ? err.message : String(err);
}

/** Bolt app in Socket Mode with the mention/DM handlers wired to handleIdea. */
export function createSlackApp(config: Config, deps: IdeaDeps): App {
	const app = new App({
		token: config.slackBotToken,
		appToken: config.slackAppToken,
		socketMode: true,
	});

	const respond = async (channel: string, threadTs: string, idea: string) => {
		const placeholder = await app.client.chat.postMessage({
			channel,
			thread_ts: threadTs,
			text: ":hourglass_flowing_sand: Drafting a PRD…",
		});
		const ts = placeholder.ts;
		try {
			const reply = await handleIdea(idea, deps);
			await app.client.chat.update({ channel, ts: ts ?? "", text: reply });
		} catch (err) {
			await app.client.chat.update({
				channel,
				ts: ts ?? "",
				text: `:warning: Couldn't draft a PRD: ${errorText(err)}`,
			});
		}
	};

	app.event("app_mention", async ({ event }) => {
		const idea = event.text.replace(/<@[A-Z0-9]+>/g, "").trim();
		if (!idea) return;
		await respond(event.channel, event.thread_ts ?? event.ts, idea);
	});

	app.message(async ({ message }) => {
		// DMs only; ignore edits, bot messages, and other subtypes.
		if (message.subtype !== undefined) return;
		if (message.channel_type !== "im") return;
		const idea = message.text?.trim();
		if (!idea) return;
		await respond(message.channel, message.thread_ts ?? message.ts, idea);
	});

	return app;
}
