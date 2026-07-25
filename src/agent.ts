import {
	Agent,
	type AgentTool,
	type AgentToolResult,
	type StreamFn,
} from "@earendil-works/pi-agent-core";
import {
	type Api,
	contentText,
	createModels,
	type Model,
	type MutableModels,
	Type,
} from "@earendil-works/pi-ai";
import { anthropicProvider } from "@earendil-works/pi-ai/providers/anthropic";
import { googleProvider } from "@earendil-works/pi-ai/providers/google";
import { openaiProvider } from "@earendil-works/pi-ai/providers/openai";
import type { Provider } from "./config.ts";
import type { GitHubClient } from "./github.ts";
import { buildSystemPrompt } from "./prompts.ts";

export interface UserStory {
	title: string;
	description: string;
	acceptanceCriteria: string[];
}

export interface Prd {
	title: string;
	summary: string;
	problem: string;
	goals: string[];
	nonGoals: string[];
	userStories: UserStory[];
}

export interface FiledIssue {
	title: string;
	body: string;
	/** Present when a real issue was created. */
	number?: number;
	url?: string;
	/** True when issue creation was skipped and this is only the would-be payload. */
	dryRun: boolean;
}

export interface FileIssuesResult {
	filed: FiledIssue[];
	failed: { title: string; error: string }[];
}

export interface HistoryEntry {
	role: "user" | "assistant";
	text: string;
}

export interface PrdAgentDeps {
	models: MutableModels;
	model: Model<Api>;
	/** Abort the run after this many milliseconds. */
	timeoutMs: number;
	/** Test seam: defaults to models.streamSimple. */
	streamFn?: StreamFn;
}

/** Shared model registry for one provider (auth via the provider's API key env var). */
export function buildModels(provider: Provider): MutableModels {
	const models = createModels();
	switch (provider) {
		case "openai":
			models.setProvider(openaiProvider());
			break;
		case "anthropic":
			models.setProvider(anthropicProvider());
			break;
		case "google":
			models.setProvider(googleProvider());
			break;
	}
	return models;
}

const userStorySchema = Type.Object({
	title: Type.String({ description: 'Story title, e.g. "As a ... I want ... so that ..."' }),
	description: Type.String({ description: "1-3 sentences expanding on the story" }),
	acceptanceCriteria: Type.Array(Type.String(), { description: "2-4 testable criteria" }),
});

const prdSchema = Type.Object({
	title: Type.String(),
	summary: Type.String({ description: "One-paragraph overview" }),
	problem: Type.String({ description: "Problem statement" }),
	goals: Type.Array(Type.String()),
	nonGoals: Type.Array(Type.String()),
	userStories: Type.Array(userStorySchema),
});

/** Prepend conversation history to the raw idea in a simple readable format. */
export function formatPrompt(idea: string, history?: HistoryEntry[]): string {
	if (!history || history.length === 0) return idea;
	const lines = history.map((h) => `${h.role === "user" ? "User" : "Assistant"}: ${h.text}`);
	return `Conversation so far:\n${lines.join("\n")}\n\nLatest request: ${idea}`;
}

/**
 * Run the PRD-drafting agent over a product idea. The LLM only drafts — its
 * sole tool is the terminal submit_prd. Creates a fresh Agent per call (one
 * active run per Agent instance); the `models` registry is shared.
 */
export async function runPrdAgent(
	idea: string,
	deps: PrdAgentDeps,
	history?: HistoryEntry[],
): Promise<Prd> {
	let capturedPrd: Prd | undefined;

	const submitPrdTool: AgentTool<typeof prdSchema, Prd> = {
		name: "submit_prd",
		label: "Submit PRD",
		description: "Submit the final structured PRD. Call exactly once. Ends the run.",
		parameters: prdSchema,
		execute: async (_toolCallId, params): Promise<AgentToolResult<Prd>> => {
			capturedPrd = params;
			return {
				content: [{ type: "text", text: `PRD "${params.title}" submitted.` }],
				details: params,
				terminate: true,
			};
		},
	};

	const agent = new Agent({
		initialState: {
			systemPrompt: buildSystemPrompt(),
			model: deps.model,
			tools: [submitPrdTool],
		},
		streamFn: deps.streamFn ?? deps.models.streamSimple.bind(deps.models),
	});

	let timedOut = false;
	const timer = setTimeout(() => {
		timedOut = true;
		agent.abort();
	}, deps.timeoutMs);
	try {
		await agent.prompt(formatPrompt(idea, history));
	} finally {
		clearTimeout(timer);
	}

	if (timedOut) {
		throw new Error(`Agent run timed out after ${deps.timeoutMs} ms`);
	}
	if (agent.state.errorMessage) {
		throw new Error(`Agent run failed: ${agent.state.errorMessage}`);
	}
	if (!capturedPrd) {
		const lastAssistant = agent.state.messages.findLast((m) => m.role === "assistant");
		const fallback = lastAssistant ? contentText(lastAssistant.content) : "";
		throw new Error(
			`Agent finished without submitting a PRD.${fallback ? ` Last assistant message: ${fallback}` : ""}`,
		);
	}

	return capturedPrd;
}

/** Issue body for a user story: description plus acceptance criteria checklist. */
export function storyBody(story: UserStory): string {
	const criteria = story.acceptanceCriteria.map((c) => `- [ ] ${c}`).join("\n");
	return `${story.description}\n\n## Acceptance criteria\n${criteria}`;
}

/**
 * File one GitHub issue per user story. Deterministic — the LLM proposes,
 * this code disposes. Per-issue failures are caught and reported, not thrown.
 */
export async function fileIssues(
	prd: Prd,
	github: GitHubClient,
	dryRun: boolean,
): Promise<FileIssuesResult> {
	const result: FileIssuesResult = { filed: [], failed: [] };
	for (const story of prd.userStories) {
		const body = storyBody(story);
		if (dryRun) {
			result.filed.push({ title: story.title, body, dryRun: true });
			continue;
		}
		try {
			const created = await github.createIssue(story.title, body);
			result.filed.push({
				title: story.title,
				body,
				number: created.number,
				url: created.url,
				dryRun: false,
			});
		} catch (err) {
			result.failed.push({
				title: story.title,
				error: err instanceof Error ? err.message : String(err),
			});
		}
	}
	return result;
}
