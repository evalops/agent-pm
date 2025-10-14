"""Wrapper for OpenAI Responses API usage."""

from __future__ import annotations

import hashlib
from typing import Any

from openai import OpenAI

from ..settings import settings


class OpenAIClient:
    def __init__(self) -> None:
        self._api_key: str | None = settings.openai_api_key
        self._client: OpenAI | None = None

    def _ensure_client(self) -> OpenAI:
        api_key = settings.openai_api_key
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required unless DRY_RUN=true")

        if self._client is None or self._api_key != api_key:
            self._client = OpenAI(api_key=api_key)
            self._api_key = api_key
        return self._client

    def _dry_run_response(self, user_prompt: str) -> str:
        digest = hashlib.sha1(user_prompt.encode()).hexdigest()[:8]
        return f"[dry-run] plan stub #{digest}"

    def create_plan(
        self,
        system_prompt: str,
        user_prompt: str,
        tools: list[dict[str, Any]],
        model: str = "gpt-4.1-mini",
        temperature: float = 0.2,
    ) -> str:
        if not settings.openai_api_key:
            if settings.dry_run:
                return self._dry_run_response(user_prompt)
            raise RuntimeError("OPENAI_API_KEY not configured")

        client = self._ensure_client()
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tools=tools,
            temperature=temperature,
        )
        return response.output_text


def _build_client() -> OpenAIClient:
    return OpenAIClient()


openai_client = _build_client()


__all__ = ["openai_client", "OpenAIClient"]
