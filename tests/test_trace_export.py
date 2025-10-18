"""Tests for trace export utilities."""

import json

import pytest

from agent_pm.observability.export import schedule_trace_export


@pytest.mark.asyncio
async def test_schedule_trace_export_no_config(tmp_path):
    """Test that export runs without errors when no backends configured."""
    trace_file = tmp_path / "test_trace.json"
    trace_file.write_text(json.dumps({"trace_id": "test", "events": []}))
    # Should not raise
    schedule_trace_export(trace_file)


@pytest.mark.asyncio
async def test_schedule_trace_export_missing_file(tmp_path):
    """Test graceful handling of missing trace file."""
    missing_file = tmp_path / "nonexistent.json"
    # Should not raise
    schedule_trace_export(missing_file)
