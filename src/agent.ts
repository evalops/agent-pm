import { Agent, type AgentTool, type AgentToolResult } from "@earendil-works/pi-agent-core";
import {
	type Api,
	contentText,
	createModels,
	type Model,
	type MutableModels,
	Type,
} from "@earendil-works/pi-ai";
import { openaiProvider } from "@earendil-works/pi-ai/providers/openai";
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

export interface PrdRunResult {
	prd: Prd;
	issues: FiledIssue[];
}

export interface PrdAgentDeps {
	models: MutableModels;
	model: Model<Api>;
	github: GitHubClient;
	dryRun: boolean;
}

/** Shared model registry with the OpenAI provider (auth via OPENAI_API_KEY). */
export function buildModels(): MutableModels {
	const models = createModels();
	models.setProvider(openaiProvider());
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

/**
 * Run the PRD agent over a raw product idea. Creates a fresh Agent per call
 * (one active run per Agent instance); the `models` registry is shared.
 */
export async function runPrdAgent(idea: string, deps: PrdAgentDeps): Promise<PrdRunResult> {
	let capturedPrd: Prd | undefined;
	const issues: FiledIssue[] = [];

	const createIssueSchema = Type.Object({
		title: Type.String(),
		body: Type.String({ description: "Issue body in markdown" }),
	});

	const createIssueTool: AgentTool<typeof createIssueSchema> = {
		name: "create_github_issue",
		label: "Create GitHub Issue",
		description:
			"File a GitHub issue for one user story. Call once per user story, after drafting them.",
		parameters: createIssueSchema,
		execute: async (_toolCallId, params) => {
			if (deps.dryRun) {
				const issue: FiledIssue = { title: params.title, body: params.body, dryRun: true };
				issues.push(issue);
				return {
					content: [
						{
							type: "text",
							text: `Dry run: issue "${params.title}" was NOT created. Payload recorded.`,
						},
					],
					details: issue,
				};
			}
			const created = await deps.github.createIssue(params.title, params.body);
			const issue: FiledIssue = {
				title: params.title,
				body: params.body,
				number: created.number,
				url: created.url,
				dryRun: false,
			};
			issues.push(issue);
			return {
				content: [{ type: "text", text: `Created issue #${created.number}: ${created.url}` }],
				details: issue,
			};
		},
	};

	const submitPrdTool: AgentTool<typeof prdSchema, Prd> = {
		name: "submit_prd",
		label: "Submit PRD",
		description:
			"Submit the final structured PRD. Call exactly once, after filing issues for all user stories. Ends the run.",
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
			systemPrompt: buildSystemPrompt(deps.dryRun),
			model: deps.model,
			tools: [createIssueTool, submitPrdTool],
		},
		streamFn: deps.models.streamSimple.bind(deps.models),
	});

	await agent.prompt(idea);

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

	return { prd: capturedPrd, issues };
}
