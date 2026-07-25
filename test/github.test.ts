import { describe, expect, test } from "bun:test";
import { createIssue, makeGitHubClient } from "../src/github.ts";

describe("createIssue", () => {
	test("posts to the repo issues endpoint and returns number/url", async () => {
		let seenUrl = "";
		let seenInit: RequestInit | undefined;
		const mockFetch = (async (url: string | URL, init?: RequestInit) => {
			seenUrl = String(url);
			seenInit = init;
			return new Response(
				JSON.stringify({ number: 42, html_url: "https://github.com/o/r/issues/42" }),
				{
					status: 201,
				},
			);
		}) as typeof fetch;

		const issue = await createIssue("tok", "o/r", "Title", "Body", mockFetch);

		expect(seenUrl).toBe("https://api.github.com/repos/o/r/issues");
		if (!seenInit) throw new Error("fetch was not called");
		expect(seenInit.method).toBe("POST");
		expect((seenInit.headers as Record<string, string>).Authorization).toBe("Bearer tok");
		expect(JSON.parse(String(seenInit.body))).toEqual({ title: "Title", body: "Body" });
		expect(issue).toEqual({ number: 42, url: "https://github.com/o/r/issues/42" });
	});

	test("throws with status and body on non-2xx", async () => {
		const mockFetch = (async () =>
			new Response("validation failed", {
				status: 422,
				statusText: "Unprocessable Entity",
			})) as unknown as typeof fetch;

		await expect(createIssue("tok", "o/r", "T", "B", mockFetch)).rejects.toThrow(
			"422 Unprocessable Entity — validation failed",
		);
	});

	test("makeGitHubClient binds token and repo", async () => {
		const mockFetch = (async (url: string | URL) => {
			expect(String(url)).toBe("https://api.github.com/repos/o/r/issues");
			return new Response(JSON.stringify({ number: 1, html_url: "u" }), { status: 201 });
		}) as typeof fetch;

		const client = makeGitHubClient("tok", "o/r", mockFetch);
		const issue = await client.createIssue("t", "b");
		expect(issue.number).toBe(1);
	});
});
