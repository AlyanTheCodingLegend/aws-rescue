import streamlit as st
import boto3
from botocore.exceptions import ClientError
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from infra.config import config


@st.cache_data(ttl=30)
def get_bucket_metrics():
    """Returns (primary_count, primary_size, backup_count, backup_size, primary_ok, backup_ok)."""
    def _count(bucket, region):
        s3 = boto3.client("s3", region_name=region)
        count, size = 0, 0
        try:
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket):
                for obj in page.get("Contents", []):
                    count += 1
                    size += obj["Size"]
            return count, size, True
        except ClientError:
            return 0, 0, False

    pc, ps, p_ok = _count(config.primary_bucket, config.active_primary_region)
    bc, bs, b_ok = _count(config.backup_bucket, config.active_backup_region)
    return pc, ps, bc, bs, p_ok, b_ok


@st.cache_data(ttl=30)
def get_last_sync_time():
    dynamo = boto3.resource("dynamodb", region_name=config.dynamo_region)
    table = dynamo.Table(config.dynamo_table)
    try:
        resp = table.scan(
            FilterExpression="#s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "SUCCESS"},
        )
        items = sorted(resp.get("Items", []), key=lambda x: x.get("timestamp", ""), reverse=True)
        return items[0]["timestamp"][:19] + "Z" if items else "Never"
    except Exception:
        return "Unknown"


@st.cache_data(ttl=30)
def get_active_alerts_count():
    dynamo = boto3.resource("dynamodb", region_name=config.dynamo_region)
    table = dynamo.Table(config.dynamo_table)
    try:
        resp = table.scan(
            FilterExpression="#s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "DRIFT_DETECTED"},
        )
        return len(resp.get("Items", []))
    except Exception:
        return 0


def render_metric_cards():
    pc, ps, bc, bs, p_ok, b_ok = get_bucket_metrics()

    sync_pct = 0.0
    if pc > 0:
        in_sync = min(pc, bc)  # rough — detailed calc in drift page
        sync_pct = round(in_sync / pc * 100, 1)

    last_sync = get_last_sync_time()
    alerts = get_active_alerts_count()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Objects (Primary)", pc, help=f"s3://{config.primary_bucket}")
    with col2:
        delta_color = "normal" if sync_pct >= 95 else "inverse"
        st.metric("Sync Percentage", f"{sync_pct}%")
    with col3:
        st.metric("Last Successful Sync", last_sync)
    with col4:
        st.metric("Active Drift Alerts", alerts, delta=None)

    # Region status badges
    col_a, col_b = st.columns(2)
    with col_a:
        icon = "🟢" if p_ok else "🔴"
        st.markdown(f"{icon} **Primary Region** `{config.active_primary_region}` — `{config.primary_bucket}`")
    with col_b:
        icon = "🟢" if b_ok else "🔴"
        st.markdown(f"{icon} **Backup Region** `{config.active_backup_region}` — `{config.backup_bucket}`")
