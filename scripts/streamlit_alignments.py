"""Streamlit dashboard for goal alignment analytics."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from agent_pm.alignment_dashboard import load_alignment_data


st.set_page_config(page_title="Goal Alignment Insights", layout="wide")
st.title("Goal Alignment Insights")

with st.sidebar:
    st.header("Configuration")
    limit = st.slider("Events to load", min_value=10, max_value=200, value=50, step=10)
    refresh = st.button("Refresh data", use_container_width=True)


@st.cache_data(ttl=60)
def _get_alignment_data(limit_value: int):
    return load_alignment_data(limit=limit_value)


if refresh:
    _get_alignment_data.clear()

events, summary, source = _get_alignment_data(limit)

st.caption(f"Data source: {source.upper()} (last {summary.get('total_events', len(events))} events)")

status_counts = summary.get("status_counts", {})
status_keys = sorted(status_counts.keys())

col1, col2, col3 = st.columns(3)
col1.metric("Total Events", summary.get("total_events", len(events)))
col2.metric("Success Notifications", status_counts.get("success", 0))
col3.metric("Failures", status_counts.get("error", 0) + status_counts.get("failed", 0))

status_filter = st.multiselect("Filter by status", options=status_keys, default=status_keys)
search_text = st.text_input("Search by initiative or idea")


def _build_dataframe(raw_events: list[dict[str, object]]) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for event in raw_events:
        notification = event.get("notification", {})
        status = notification.get("status", "unknown")
        suggestions = event.get("suggestions", [])
        if not suggestions:
            records.append(
                {
                    "title": event.get("title"),
                    "status": status,
                    "idea": None,
                    "overlapping_goals": None,
                    "similarity": None,
                    "created_at": event.get("created_at"),
                }
            )
            continue
        for suggestion in suggestions:
            records.append(
                {
                    "title": event.get("title"),
                    "status": status,
                    "idea": suggestion.get("idea"),
                    "overlapping_goals": ", ".join(suggestion.get("overlapping_goals", [])),
                    "similarity": suggestion.get("similarity"),
                    "created_at": event.get("created_at"),
                }
            )
    df = pd.DataFrame.from_records(records)
    if not df.empty:
        df["created_at"] = df["created_at"].apply(lambda ts: ts or None)
    return df


df = _build_dataframe(events)

if status_filter:
    df = df[df["status"].isin(status_filter)]

if search_text:
    lowered = search_text.lower()
    df = df[df.apply(lambda row: lowered in str(row.get("title", "")).lower() or lowered in str(row.get("idea", "")).lower(), axis=1)]

st.subheader("Alignment Matches")
if df.empty:
    st.info("No alignment events available with the current filters.")
else:
    df_display = df.copy()
    if "similarity" in df_display.columns:
        df_display["similarity"] = df_display["similarity"].apply(lambda v: f"{v:.2f}" if isinstance(v, (int, float)) else v)
    st.dataframe(df_display, use_container_width=True)

if summary.get("top_ideas"):
    st.subheader("Top Overlapping Initiatives")
    top_ideas_df = pd.DataFrame(summary["top_ideas"], columns=["idea", "count"])
    st.bar_chart(top_ideas_df.set_index("idea"))

st.caption("Last refreshed: %s" % datetime.utcnow().isoformat())
