"""Streamlit dashboard for goal alignment analytics."""

from __future__ import annotations

import os
import time
from datetime import datetime

import pandas as pd
import streamlit as st

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
    plugin_rows = []
    stats_rows = []
    for item in plugins:
        plugin_rows.append(
            {
                "name": item.get("name"),
                "enabled": item.get("enabled"),
                "active": item.get("active"),
                "hooks": ", ".join(item.get("hooks", [])),
            }
        )
        for hook, counts in (item.get("hook_stats") or {}).items():
            stats_rows.append(
                {
                    "plugin": item.get("name"),
                    "hook": hook,
                    "invocations": counts.get("invocations", 0),
                    "failures": counts.get("failures", 0),
                }
            )

    st.dataframe(pd.DataFrame(plugin_rows), use_container_width=True)

    if stats_rows:
        stats_df = pd.DataFrame(stats_rows)
        st.dataframe(stats_df, use_container_width=True)
        pivot = stats_df.pivot_table(index="hook", columns="plugin", values="invocations", fill_value=0)
        st.bar_chart(pivot)

st.caption("Plugin data source: %s" % plugin_source.upper())
st.caption("Last refreshed: %s" % datetime.utcnow().isoformat())
