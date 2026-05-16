from __future__ import annotations

import os
from pathlib import Path

import pytest


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    test_tmp = Path(os.environ.get("TEST_TMPDIR", repo_root / ".bazel-test-tmp"))
    data_dir = test_tmp / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    os.chdir(test_tmp)
    os.environ.setdefault("DRY_RUN", "true")
    os.environ.setdefault("OPENAI_API_KEY", "test-key")
    os.environ.setdefault("ALIGNMENT_LOG_PATH", str(data_dir / "alignment_log.json"))
    os.environ.setdefault("AGENTS_SESSION_DB", str(data_dir / "agent_sessions.db"))
    os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{data_dir / 'agent_pm.db'}")
    os.environ.setdefault("AGENTS_CONFIG_PATH", str(repo_root / "config" / "agents.yaml"))
    os.environ.setdefault("PLUGIN_CONFIG_PATH", str(repo_root / "config" / "plugins.yaml"))
    os.environ.setdefault("PROCEDURE_DIR", str(repo_root / "procedures"))
    os.environ.setdefault("TRACE_DIR", str(data_dir / "traces"))
    os.environ.setdefault("TOOL_CONFIG_PATH", str(repo_root / "config" / "tools.yaml"))
    os.environ.setdefault("VECTOR_STORE_PATH", str(data_dir / "vector_store.json"))
    return pytest.main([str(repo_root / "tests"), "-c", str(repo_root / "pyproject.toml"), "-p", "no:cacheprovider"])


if __name__ == "__main__":
    raise SystemExit(main())
