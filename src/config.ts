export interface Config {
	slackBotToken: string;
	slackAppToken: string;
	openaiApiKey: string;
	githubToken: string;
	/** "owner/name" */
	githubRepo: string;
	dryRun: boolean;
	model: string;
}

const REQUIRED = [
	"SLACK_BOT_TOKEN",
	"SLACK_APP_TOKEN",
	"OPENAI_API_KEY",
	"GITHUB_TOKEN",
	"GITHUB_REPO",
] as const;

/** Parse and validate configuration from an environment map (defaults to process.env). */
export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
	const missing = REQUIRED.filter((key) => !env[key]?.trim());
	if (missing.length > 0) {
		throw new Error(`Missing required environment variables: ${missing.join(", ")}`);
	}

	const githubRepo = env.GITHUB_REPO?.trim() ?? "";
	if (!/^[\w.-]+\/[\w.-]+$/.test(githubRepo)) {
		throw new Error(`GITHUB_REPO must be in "owner/name" form, got: ${githubRepo}`);
	}

	// DRY_RUN defaults to true: only an explicit "false"/"0"/"no" disables it.
	const rawDryRun = env.DRY_RUN?.trim().toLowerCase();
	const dryRun = rawDryRun === undefined || !["false", "0", "no"].includes(rawDryRun);

	return {
		slackBotToken: env.SLACK_BOT_TOKEN?.trim() ?? "",
		slackAppToken: env.SLACK_APP_TOKEN?.trim() ?? "",
		openaiApiKey: env.OPENAI_API_KEY?.trim() ?? "",
		githubToken: env.GITHUB_TOKEN?.trim() ?? "",
		githubRepo,
		dryRun,
		model: env.MODEL?.trim() || "gpt-4o-mini",
	};
}
