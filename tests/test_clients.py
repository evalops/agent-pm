import asyncio
from datetime import UTC, datetime

from agent_pm.clients import calendar_client, slack_client


def test_slack_dry_run_returns_payload():
    result = asyncio.run(slack_client.post_digest("*Update*", "#demo"))
    assert result["dry_run"] is True
    assert result["payload"]["channel"] == "#demo"


def test_slack_empty_body_raises_value_error():
    try:
        asyncio.run(slack_client.post_digest("", "#demo"))
    except ValueError as exc:
        assert "must not be empty" in str(exc)
    else:  # pragma: no cover - ensures failure if no error is raised
        raise AssertionError("Expected ValueError for empty body")


def test_calendar_dry_run_returns_event():
    start = datetime.now(UTC)
    result = asyncio.run(
        calendar_client.schedule_review(
            summary="Stakeholder review",
            description="Discuss roadmap",
            start_time=start,
            duration_minutes=30,
            attendees=["demo@example.com"],
        )
    )
    assert result["dry_run"] is True
    assert result["event"]["summary"] == "Stakeholder review"


def test_calendar_duration_validation():
    start = datetime.now(UTC)
    try:
        asyncio.run(
            calendar_client.schedule_review(
                summary="Invalid",
                description="",
                start_time=start,
                duration_minutes=0,
            )
        )
    except ValueError as exc:
        assert "must be positive" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected ValueError for non-positive duration")
