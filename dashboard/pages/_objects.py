import streamlit as st
import boto3
import pandas as pd
from botocore.exceptions import ClientError
from datetime import datetime
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from infra.config import config


def _human_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


@st.cache_data(ttl=30)
def _list_bucket(bucket: str, region: str) -> list[dict]:
    s3 = boto3.client("s3", region_name=region)
    objects = []
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            for obj in page.get("Contents", []):
                objects.append({
                    "key": obj["Key"],
                    "size": obj["Size"],
                    "size_hr": _human_size(obj["Size"]),
                    "last_modified": obj["LastModified"].strftime("%Y-%m-%d %H:%M:%S"),
                    "etag": obj["ETag"].strip('"'),
                })
    except ClientError as e:
        st.error(f"Cannot list bucket {bucket}: {e.response['Error']['Message']}")
    return objects


@st.cache_data(ttl=60)
def _get_object_history(object_key: str) -> list[dict]:
    dynamo = boto3.resource("dynamodb", region_name=config.dynamo_region)
    table = dynamo.Table(config.dynamo_table)
    try:
        resp = table.query(
            KeyConditionExpression="object_key = :k",
            ExpressionAttributeValues={":k": object_key},
            ScanIndexForward=False,
            Limit=20,
        )
        return resp.get("Items", [])
    except Exception:
        return []


def render():
    st.title("📦 Object Browser")

    search = st.text_input("🔍 Filter by key prefix or substring", "")

    primary_objs = _list_bucket(config.primary_bucket, config.active_primary_region)
    backup_objs = _list_bucket(config.backup_bucket, config.active_backup_region)

    backup_keys = {o["key"]: o["etag"] for o in backup_objs}
    primary_keys = {o["key"]: o["etag"] for o in primary_objs}

    def _filter(objs):
        if search:
            return [o for o in objs if search.lower() in o["key"].lower()]
        return objs

    primary_filtered = _filter(primary_objs)
    backup_filtered = _filter(backup_objs)

    col1, col2 = st.columns(2)

    with col1:
        st.subheader(f"Primary — `{config.primary_bucket}`")
        st.caption(f"{config.active_primary_region} · {len(primary_filtered)} objects")
        for obj in primary_filtered:
            in_backup = backup_keys.get(obj["key"]) == obj["etag"]
            icon = "🟢" if in_backup else "🔴"
            with st.expander(f"{icon} {obj['key']} ({obj['size_hr']})"):
                st.markdown(f"- **Last Modified:** {obj['last_modified']}")
                st.markdown(f"- **ETag:** `{obj['etag']}`")
                st.markdown(f"- **In backup:** {'✅ Yes' if in_backup else '❌ No'}")
                if st.button("Show replication history", key=f"ph_{obj['key']}"):
                    history = _get_object_history(obj["key"])
                    if history:
                        df = pd.DataFrame(history)[["timestamp", "status", "size_bytes", "source_region", "dest_region"]]
                        st.dataframe(df, use_container_width=True)
                    else:
                        st.info("No replication history found.")

    with col2:
        st.subheader(f"Backup — `{config.backup_bucket}`")
        st.caption(f"{config.active_backup_region} · {len(backup_filtered)} objects")
        for obj in backup_filtered:
            in_primary = primary_keys.get(obj["key"]) == obj["etag"]
            icon = "🟢" if in_primary else "🟡"
            with st.expander(f"{icon} {obj['key']} ({obj['size_hr']})"):
                st.markdown(f"- **Last Modified:** {obj['last_modified']}")
                st.markdown(f"- **ETag:** `{obj['etag']}`")
                st.markdown(f"- **Matches primary:** {'✅ Yes' if in_primary else '⚠️ Orphan / mismatch'}")
