import streamlit as st
import boto3
import pandas as pd
import io
import sys, os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from infra.config import config

STATUS_OPTIONS = ["ALL", "SUCCESS", "FAILED", "DRIFT_DETECTED", "DELETED", "HEALTH_CHECK_OK"]


@st.cache_data(ttl=30)
def _load_all_logs(limit: int = 1000) -> list[dict]:
    dynamo = boto3.resource("dynamodb", region_name=config.dynamo_region)
    table = dynamo.Table(config.dynamo_table)
    try:
        resp = table.scan(Limit=limit)
        return resp.get("Items", [])
    except Exception as e:
        st.error(f"Failed to load logs: {e}")
        return []


def render():
    st.title("📋 Replication Logs")

    col1, col2, col3 = st.columns(3)
    with col1:
        status_filter = st.selectbox("Status", STATUS_OPTIONS)
    with col2:
        date_from = st.date_input("From date", value=None)
    with col3:
        key_search = st.text_input("Object key contains", "")

    items = _load_all_logs()
    df = pd.DataFrame(items) if items else pd.DataFrame()

    if df.empty:
        st.info("No log entries found.")
        return

    # Normalise columns
    for col in ["object_key", "timestamp", "status", "size_bytes", "source_region",
                "dest_region", "checksum_sha256", "error_message"]:
        if col not in df.columns:
            df[col] = ""

    df["size_bytes"] = pd.to_numeric(df["size_bytes"], errors="coerce").fillna(0).astype(int)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.sort_values("timestamp", ascending=False)

    # Apply filters
    if status_filter != "ALL":
        df = df[df["status"] == status_filter]
    if date_from:
        df = df[df["timestamp"].dt.date >= date_from]
    if key_search:
        df = df[df["object_key"].str.contains(key_search, case=False, na=False)]

    display_cols = ["timestamp", "object_key", "status", "size_bytes", "source_region", "dest_region"]
    st.caption(f"Showing {len(df)} entries")

    st.dataframe(
        df[display_cols].rename(columns={
            "timestamp": "Timestamp",
            "object_key": "Object Key",
            "status": "Status",
            "size_bytes": "Size (bytes)",
            "source_region": "Source",
            "dest_region": "Destination",
        }),
        use_container_width=True,
        height=450,
    )

    # Show errors
    failed_df = df[df["status"] == "FAILED"]
    if not failed_df.empty:
        with st.expander(f"❌ Error details ({len(failed_df)} failures)"):
            for _, row in failed_df.iterrows():
                st.markdown(f"**{row['object_key']}** @ {str(row['timestamp'])[:19]}")
                st.code(row.get("error_message", "No error message"), language=None)

    # CSV download
    csv_buf = io.StringIO()
    df.to_csv(csv_buf, index=False)
    st.download_button(
        label="⬇️ Download as CSV",
        data=csv_buf.getvalue().encode("utf-8"),
        file_name=f"rescue_logs_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv",
    )
