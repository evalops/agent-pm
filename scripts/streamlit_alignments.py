"""Streamlit dashboard for goal alignment analytics."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st
import requests

from agent_pm.alignment_dashboard import (
    flatten_alignment_records,
    load_alignment_data,
    load_plugin_metadata,
    followup_conversion,
    status_counts_by_idea,
    status_trend_by_day,
)


st.set_page_config(page_title="Goal Alignment Insights", layout="wide")
st.title("Goal Alignment Insights")

TOKEN = os.getenv("ALIGNMENT_DASHBOARD_TOKEN")

if TOKEN:
    if "auth_ok" not in st.session_state:
        st.session_state.auth_ok = False
    with st.sidebar:
        st.header("Authentication")
        provided = st.text_input("Access token", type="password")
        if st.button("Unlock"):
            st.session_state.auth_ok = provided == TOKEN
        if not st.session_state.get("auth_ok"):
            st.warning("Enter the access token to view the dashboard.")
            st.stop()

with st.sidebar:
    st.header("Configuration")
    limit = st.slider("Events to load", min_value=10, max_value=200, value=50, step=10)
    auto_refresh = st.checkbox("Auto-refresh", value=True)
    refresh_interval = st.number_input("Refresh interval (seconds)", min_value=15, max_value=300, value=60, step=15)
    refresh = st.button("Refresh now", use_container_width=True)
    plugin_api = st.text_input("Plugins API", value=os.getenv("PLUGINS_API_URL", ""))
    plugin_api_key = st.text_input("Plugins API Key", value=os.getenv("PLUGINS_API_KEY", ""), type="password")


@st.cache_data(ttl=60)
def _get_alignment_data(limit_value: int):
    return load_alignment_data(limit=limit_value)


if refresh:
    _get_alignment_data.clear()

if auto_refresh:
    last = st.session_state.get("_auto_refresh_last", 0.0)
    now = time.time()
    if now - last >= refresh_interval:
        st.session_state["_auto_refresh_last"] = now
    else:
        delay = max(refresh_interval - (now - last), 0.5)
        time.sleep(delay)
        st.session_state["_auto_refresh_last"] = time.time()
        st.experimental_rerun()


events, summary, source = _get_alignment_data(limit)

records = flatten_alignment_records(events)
df = pd.DataFrame.from_records(records)
conversion = followup_conversion(events)

st.caption(f"Data source: {source.upper()} (last {summary.get('total_events', len(events))} events)")


@st.cache_data(ttl=60)
def _get_plugin_registry(api_url: str, api_key: str | None):
    return load_plugin_metadata(api_url=api_url or None, api_key=api_key or None)


def _plugin_api_request(
    api_url: str,
    api_key: str | None,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not api_url:
        raise ValueError("Plugin API URL is required for this action")
    url = api_url.rstrip("/") + path
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    response = requests.request(method, url, json=payload, headers=headers, timeout=10)
    response.raise_for_status()
    return response.json()


plugins, plugin_source = _get_plugin_registry(plugin_api.strip(), plugin_api_key.strip())

status_counts = summary.get("status_counts", {})
status_keys = sorted(status_counts.keys())

col1, col2, col3 = st.columns(3)
col1.metric("Total Events", summary.get("total_events", len(events)))
col2.metric("Success Notifications", status_counts.get("success", 0))
col3.metric("Failures", status_counts.get("error", 0) + status_counts.get("failed", 0))

st.subheader("Follow-up Conversion")
followup_total = sum(conversion["followup_counts"].values())
overall_rate = conversion["rates"].get("overall", 0.0)
colf1, colf2, colf3 = st.columns(3)
colf1.metric("Follow-ups Logged", followup_total)
colf2.metric("Distinct Follow-up Outcomes", len(conversion["followup_counts"]))
colf3.metric("Overall Follow-up Rate", f"{overall_rate*100:.1f}%" if overall_rate else "0%")

if conversion["per_notification"]:
    per_df = pd.DataFrame(conversion["per_notification"]).fillna(0).T
    st.bar_chart(per_df)


status_filter = st.multiselect("Filter by status", options=status_keys, default=status_keys)
channel_options: list[str] = []
if not df.empty and "channel" in df.columns:
    channel_options = sorted([value for value in df["channel"].dropna().unique() if value])
channel_filter = st.multiselect("Filter by Slack channel", options=channel_options, default=channel_options)
followup_options: list[str] = []
if not df.empty and "followup_status" in df.columns:
    followup_options = sorted([value for value in df["followup_status"].dropna().unique() if value])
followup_filter = st.multiselect("Filter by follow-up status", options=followup_options, default=followup_options)
search_text = st.text_input("Search by initiative or idea")

if not df.empty:
    if status_filter:
        df = df[df["status"].isin(status_filter)]
    if channel_filter:
        df = df[df["channel"].isin(channel_filter)]
    if followup_filter:
        df = df[df["followup_status"].isin(followup_filter)]
    if search_text:
        lowered = search_text.lower()
        df = df[
            df.apply(
                lambda row: lowered in str(row.get("title", "")).lower()
                or lowered in str(row.get("idea", "")).lower(),
                axis=1,
            )
        ]

st.subheader("Alignment Matches")
if df.empty:
    st.info("No alignment events available with the current filters.")
else:
    df_display = df.copy()
    if "overlapping_goals" in df_display.columns:
        df_display["overlapping_goals"] = df_display["overlapping_goals"].apply(
            lambda goals: ", ".join(goals) if isinstance(goals, list) else goals
        )
    if "similarity" in df_display.columns:
        df_display["similarity"] = df_display["similarity"].apply(
            lambda v: f"{v:.2f}" if isinstance(v, (int, float)) else v
        )
    if "slack_link" in df_display.columns:
        df_display["slack_link"] = df_display["slack_link"].fillna("")
    st.dataframe(
        df_display,
        use_container_width=True,
        column_config={
            "slack_link": st.column_config.LinkColumn("Slack", display_text="Open")
        },
    )

st.subheader("Status Trend")
trend_data = status_trend_by_day(events)
if trend_data:
    trend_df = pd.DataFrame(trend_data).set_index("date")
    st.area_chart(trend_df)
else:
    st.info("No status trend data available yet.")

st.subheader("Outcome by Initiative")
idea_breakdown = status_counts_by_idea(events)
if idea_breakdown:
    idea_df = pd.DataFrame(idea_breakdown).set_index("idea")
    st.bar_chart(idea_df)
else:
    st.info("No initiative outcome data to display.")

if summary.get("top_ideas"):
    st.subheader("Top Overlapping Initiatives")
    top_ideas_df = pd.DataFrame(summary["top_ideas"], columns=["idea", "count"])
    st.bar_chart(top_ideas_df.set_index("idea"))

st.subheader("Plugin Registry")
if not plugins:
    st.info("No plugin registry data available.")
else:
    summary_rows: list[dict[str, Any]] = []
    hook_rows: list[dict[str, Any]] = []
    history_lookup: dict[str, dict[str, list[dict[str, Any]]]] = {}

    for item in plugins:
        stats = item.get("hook_stats") or {}
        total_invocations = sum(entry.get("invocations", 0) for entry in stats.values())
        total_failures = sum(entry.get("failures", 0) for entry in stats.values())
        total_duration = sum(entry.get("total_duration_ms", 0.0) for entry in stats.values())
        avg_duration = round(total_duration / total_invocations, 3) if total_invocations else 0.0
        summary_rows.append(
            {
                "name": item.get("name"),
                "enabled": item.get("enabled"),
                "active": item.get("active"),
                "invocations": total_invocations,
                "failures": total_failures,
                "avg_duration_ms": avg_duration,
                "missing_secrets": ", ".join(item.get("secrets", {}).get("missing", [])),
                "errors": "; ".join(item.get("errors", [])),
                "invalid": item.get("invalid", False),
            }
        )
        for hook, counts in stats.items():
            hook_rows.append(
                {
                    "plugin": item.get("name"),
                    "hook": hook,
                    "invocations": counts.get("invocations", 0),
                    "failures": counts.get("failures", 0),
                    "avg_duration_ms": counts.get("avg_duration_ms", 0.0),
                    "last_duration_ms": counts.get("last_duration_ms", 0.0),
                }
            )
        history_lookup[item.get("name")] = {
            hook: list(entries)
            for hook, entries in (item.get("hook_history") or {}).items()
        }

    if summary_rows:
        summary_df = pd.DataFrame(summary_rows)
        st.dataframe(summary_df, use_container_width=True)

    if hook_rows:
        hook_df = pd.DataFrame(hook_rows)
        st.dataframe(hook_df, use_container_width=True)
        pivot = hook_df.pivot_table(index="hook", columns="plugin", values="invocations", fill_value=0)
        st.bar_chart(pivot)

    st.subheader("Hook Timeline")
    plugin_names = [row["name"] for row in summary_rows] if summary_rows else []
    if plugin_names:
        selected_plugin = st.selectbox("Plugin", plugin_names)
        hook_history = history_lookup.get(selected_plugin, {})
        if not hook_history:
            st.info("No hook history recorded for this plugin yet.")
        else:
            hook_options = list(hook_history.keys())
            selected_hook = st.selectbox("Hook", hook_options, key=f"hook_{selected_plugin}")
            entries = hook_history.get(selected_hook, [])
            if entries:
                history_df = pd.DataFrame(entries)
                if not history_df.empty and "timestamp" in history_df:
                    history_df["timestamp"] = pd.to_datetime(history_df["timestamp"], errors="coerce")
                    history_df = history_df.dropna(subset=["timestamp"])
                    if not history_df.empty:
                        chart_df = history_df.set_index("timestamp")["duration_ms"]
                        st.line_chart(chart_df)
                st.dataframe(history_df, use_container_width=True)
            else:
                st.info("No entries for the selected hook yet.")

    if plugin_api:
        st.subheader("Plugin Administration")
        st.caption("Actions below require Plugin API access.")
        for item in plugins:
            plugin_name = item.get("name")
            with st.expander(f"{plugin_name} controls", expanded=False):
                st.write(f"**Description:** {item.get('description', 'n/a')}")
                st.write(f"**Hooks:** {', '.join(item.get('hooks', [])) or 'none'}")
                st.write(
                    f"**Missing secrets:** {', '.join(item.get('secrets', {}).get('missing', [])) or 'none'}"
                )
                config_json = json.dumps(item.get("config") or {}, indent=2)
                with st.form(f"config_form_{plugin_name}"):
                    updated_config = st.text_area("Config (JSON)", value=config_json, height=220)
                    submitted = st.form_submit_button("Update Config")
                    if submitted:
                        try:
                            parsed_config = json.loads(updated_config) if updated_config.strip() else {}
                        except json.JSONDecodeError as exc:
                            st.error(f"Invalid JSON: {exc}")
                        else:
                            try:
                                _plugin_api_request(
                                    plugin_api,
                                    plugin_api_key,
                                    "POST",
                                    f"/plugins/{plugin_name}/config",
                                    {"config": parsed_config},
                                )
                            except Exception as exc:  # pragma: no cover - UI feedback
                                st.error(f"Failed to update config: {exc}")
                            else:
                                st.success("Config updated.")
                                _get_plugin_registry.clear()
                                st.experimental_rerun()
                cols = st.columns(2)
                if cols[0].button("Reload plugin", key=f"reload_{plugin_name}"):
                    try:
                        _plugin_api_request(plugin_api, plugin_api_key, "POST", f"/plugins/{plugin_name}/reload")
                    except Exception as exc:  # pragma: no cover - UI feedback
                        st.error(f"Reload failed: {exc}")
                    else:
                        st.success("Plugin reloaded.")
                        _get_plugin_registry.clear()
                        st.experimental_rerun()
                toggle_label = "Disable plugin" if item.get("enabled") else "Enable plugin"
                toggle_path = "/disable" if item.get("enabled") else "/enable"
                if cols[1].button(toggle_label, key=f"toggle_{plugin_name}"):
                    try:
                        _plugin_api_request(plugin_api, plugin_api_key, "POST", f"/plugins/{plugin_name}{toggle_path}")
                    except Exception as exc:
                        st.error(f"Toggle failed: {exc}")
                    else:
                        st.success(f"Plugin {toggle_label.split()[0].lower()}d.")
                        _get_plugin_registry.clear()
                        st.experimental_rerun()
    else:
        st.caption("Set PLUGINS_API_URL to enable plugin administration controls.")

st.caption("Plugin data source: %s" % plugin_source.upper())
st.caption("Last refreshed: %s" % datetime.utcnow().isoformat())
