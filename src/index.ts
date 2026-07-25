import { buildModels, runPrdAgent } from "./agent.ts";
import { loadConfig } from "./config.ts";
import { makeGitHubClient } from "./github.ts";
import { createSlackApp } from "./slack.ts";

const config = loadConfig();

const models = buildModels();
const model = models.getModel("openai", config.model);
if (!model) {
	throw new Error(`Unknown model "openai/${config.model}" — check the MODEL env var.`);
}

const github = makeGitHubClient(config.githubToken, config.githubRepo);

const app = createSlackApp(config, {
	runAgent: (idea) => runPrdAgent(idea, { models, model, github, dryRun: config.dryRun }),
});

await app.start();
console.log(
	`Agent PM listening (Socket Mode). Model: openai/${config.model}, repo: ${config.githubRepo}, dry-run: ${config.dryRun}`,
);
