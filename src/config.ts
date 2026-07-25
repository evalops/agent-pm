export type Provider = "openai" | "anthropic" | "google";

export interface Config {
	slackBotToken: string;
	slackAppToken: string;
	githubToken: string;
	/** "owner/name" */
	githubRepo: string;
	dryRun: boolean;
	provider: Provider;
	model: string;
	agentTimeoutMs: number;
}

const PROVIDERS: Provider[] = ["openai", "anthropic", "google"];

const DEFAULT_MODEL: Record<Provider, string> = {
	openai: "gpt-4o-mini",
	anthropic: "claude-sonnet-4-6",
	google: "gemini-2.5-flash",
};

/** Env var each pi provider reads its API key from. */
export const PROVIDER_API_KEY_ENV: Record<Provider, string> = {
	openai: "OPENAI_API_KEY",
	anthropic: "ANTHROPIC_API_KEY",
	google: "GEMINI_API_KEY",
};

/** Parse and validate configuration from an environment map (defaults to process.env). */
export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
	const rawProvider = env.PROVIDER?.trim() || "openai";
	if (!PROVIDERS.includes(rawProvider as Provider)) {
		throw new Error(`PROVIDER must be one of ${PROVIDERS.join(", ")}, got: ${rawProvider}`);
	}
	const provider = rawProvider as Provider;

	const required = ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "GITHUB_TOKEN", "GITHUB_REPO"];
	const missing = required.filter((key) => !env[key]?.trim());
	if (missing.length > 0) {
		throw new Error(`Missing required environment variables: ${missing.join(", ")}`);
	}

	const apiKeyEnv = PROVIDER_API_KEY_ENV[provider];
	if (!env[apiKeyEnv]?.trim()) {
		throw new Error(
			`Missing required environment variable for provider "${provider}": ${apiKeyEnv}`,
		);
	}

	const githubRepo = env.GITHUB_REPO?.trim() ?? "";
	if (!/^[\w.-]+\/[\w.-]+$/.test(githubRepo)) {
		throw new Error(`GITHUB_REPO must be in "owner/name" form, got: ${githubRepo}`);
	}

	// DRY_RUN defaults to true: only an explicit "false"/"0"/"no" disables it.
	const rawDryRun = env.DRY_RUN?.trim().toLowerCase();
	const dryRun = rawDryRun === undefined || !["false", "0", "no"].includes(rawDryRun);

	const rawTimeout = env.AGENT_TIMEOUT_MS?.trim();
	const agentTimeoutMs = rawTimeout === undefined ? 180_000 : Number.parseInt(rawTimeout, 10);
	if (!Number.isFinite(agentTimeoutMs) || agentTimeoutMs <= 0) {
		throw new Error(`AGENT_TIMEOUT_MS must be a positive integer, got: ${rawTimeout}`);
	}

	return {
		slackBotToken: env.SLACK_BOT_TOKEN?.trim() ?? "",
		slackAppToken: env.SLACK_APP_TOKEN?.trim() ?? "",
		githubToken: env.GITHUB_TOKEN?.trim() ?? "",
		githubRepo,
		dryRun,
		provider,
		model: env.MODEL?.trim() || DEFAULT_MODEL[provider],
		agentTimeoutMs,
	};
}
