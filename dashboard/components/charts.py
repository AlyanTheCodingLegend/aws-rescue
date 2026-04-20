import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import boto3
import sys, os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from infra.config import config


@st.cache_data(ttl=60)
def _load_replication_log(limit: int = 500):
    dynamo = boto3.resource("dynamodb", region_name=config.dynamo_region)
    table = dynamo.Table(config.dynamo_table)
    try:
        resp = table.scan(Limit=limit)
        return resp.get("Items", [])
    except Exception:
        return []


def render_replication_timeline():
    st.subheader("Replication Timeline")
    items = _load_replication_log()
    if not items:
        st.info("No replication log data yet.")
        return

    df = pd.DataFrame(items)
    df = df[df["status"] == "SUCCESS"].copy()
    if df.empty:
        st.info("No successful replications logged yet.")
        return

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"])
    df["hour"] = df["timestamp"].dt.floor("h")
    timeline = df.groupby("hour").size().reset_index(name="replications")

    fig = px.line(
        timeline, x="hour", y="replications",
        title="Objects Replicated per Hour",
        labels={"hour": "Time (UTC)", "replications": "Count"},
        markers=True,
    )
    fig.update_layout(height=300, margin=dict(l=0, r=0, t=40, b=0))
    st.plotly_chart(fig, use_container_width=True)


def render_drift_chart():
    st.subheader("Drift History")
    items = _load_replication_log()
    if not items:
        st.info("No data yet.")
        return

    df = pd.DataFrame(items)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"])
    df["day"] = df["timestamp"].dt.floor("D")

    status_counts = df.groupby(["day", "status"]).size().reset_index(name="count")

    fig = px.bar(
        status_counts, x="day", y="count", color="status",
        title="Daily Replication Events by Status",
        color_discrete_map={
            "SUCCESS": "#22c55e",
            "FAILED": "#ef4444",
            "DRIFT_DETECTED": "#f59e0b",
            "DELETED": "#94a3b8",
            "HEALTH_CHECK_OK": "#3b82f6",
        },
        labels={"day": "Date", "count": "Events"},
    )
    fig.update_layout(height=300, margin=dict(l=0, r=0, t=40, b=0))
    st.plotly_chart(fig, use_container_width=True)
