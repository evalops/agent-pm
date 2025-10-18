import asyncio
from types import SimpleNamespace

from agent_pm import dspy_program, embeddings, openai_utils
from agent_pm.prd import changelog


def test_generate_embedding_dry_run_returns_stub(monkeypatch):
    dummy_settings = SimpleNamespace(openai_api_key=None, dry_run=True)
    monkeypatch.setattr(embeddings, "settings", dummy_settings, raising=False)
    monkeypatch.setattr(openai_utils, "settings", dummy_settings, raising=False)

    result = asyncio.run(embeddings.generate_embedding("hello world"))

    assert len(result) == 1536
    assert all(0.0 <= value <= 1.0 for value in result)


def test_compile_brief_dry_run_skips_remote_call(monkeypatch):
    dummy_settings = SimpleNamespace(openai_api_key=None, dry_run=True)
    monkeypatch.setattr(dspy_program, "settings", dummy_settings, raising=False)
    monkeypatch.setattr(openai_utils, "settings", dummy_settings, raising=False)

    dspy_program._configured_program.cache_clear()

    result = dspy_program.compile_brief("title", "context", ["constraint"])

    assert result == ""


def test_generate_changelog_dry_run_returns_summary(monkeypatch):
    dummy_settings = SimpleNamespace(openai_api_key=None, dry_run=True)
    monkeypatch.setattr(changelog, "settings", dummy_settings, raising=False)
    monkeypatch.setattr(openai_utils, "settings", dummy_settings, raising=False)

    diff_summary = {"additions": 3, "deletions": 1}

    result = asyncio.run(changelog.generate_changelog("old", "new", diff_summary))

    assert result == "**Changes:** 3 additions, 1 deletions"
