from agent_pm.metrics import (
    latest_metrics,
    record_guardrail_rejection,
    record_planner_request,
    record_revisions,
    record_tool_invocation,
)


def test_metrics_emit() -> None:
    with record_planner_request():
        pass
    record_guardrail_rejection("planner_input")
    record_revisions(2)
    record_tool_invocation("create_jira_issue", "success")

    output = latest_metrics().decode()
    assert "planner_requests_total" in output
    assert "planner_guardrail_rejections_total" in output
    assert "planner_revisions_total" in output
    assert "tool_invocations_total" in output
