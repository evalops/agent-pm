import { buildModels, runPrdAgent } from "./agent.ts";
import { loadConfig } from "./config.ts";
import { makeGitHubClient } from "./github.ts";
import { createSlackApp } from "./slack.ts";

const config = loadConfig();

const models = buildModels(config.provider);
const model = models.getModel(config.provider, config.model);
if (!model) {
	throw new Error(`Unknown model "${config.provider}/${config.model}" — check the MODEL env var.`);
}

const github = makeGitHubClient(config.githubToken, config.githubRepo);

const app = createSlackApp(config, {
	runAgent: (idea, history) =>
		runPrdAgent(idea, { models, model, timeoutMs: config.agentTimeoutMs }, history),
	github,
	dryRun: config.dryRun,
});

await app.start();
console.log(
	`Agent PM listening (Socket Mode). Model: ${config.provider}/${config.model}, repo: ${config.githubRepo}, dry-run: ${config.dryRun}`,
);
