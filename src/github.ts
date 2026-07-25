export interface CreatedIssue {
	number: number;
	url: string;
}

export interface GitHubClient {
	createIssue(title: string, body: string): Promise<CreatedIssue>;
}

type FetchLike = typeof fetch;

/** POST a new issue to api.github.com. Throws with status + body on non-2xx. */
export async function createIssue(
	token: string,
	repo: string,
	title: string,
	body: string,
	fetchImpl: FetchLike = fetch,
): Promise<CreatedIssue> {
	const res = await fetchImpl(`https://api.github.com/repos/${repo}/issues`, {
		method: "POST",
		headers: {
			Accept: "application/vnd.github+json",
			Authorization: `Bearer ${token}`,
			"Content-Type": "application/json",
			"User-Agent": "agent-pm",
			"X-GitHub-Api-Version": "2022-11-28",
		},
		body: JSON.stringify({ title, body }),
	});

	if (!res.ok) {
		const text = await res.text().catch(() => "");
		throw new Error(`GitHub create issue failed: ${res.status} ${res.statusText} — ${text}`);
	}

	const data = (await res.json()) as { number: number; html_url: string };
	return { number: data.number, url: data.html_url };
}

/** A GitHubClient bound to a token and repository. */
export function makeGitHubClient(
	token: string,
	repo: string,
	fetchImpl: FetchLike = fetch,
): GitHubClient {
	return {
		createIssue: (title, body) => createIssue(token, repo, title, body, fetchImpl),
	};
}
