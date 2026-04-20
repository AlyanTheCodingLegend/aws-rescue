"""
Controls page — manual operations: sync, health check, simulate outage, seed data.
Outage simulation disables/re-enables the S3 → Lambda trigger on the primary bucket.
Notifications appear in-dashboard (no SNS/email).
"""
import streamlit as st
import boto3
import json
import sys, os
from botocore.exceptions import ClientError
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from infra.config import config


def _s3_pri():
    return boto3.client("s3", region_name=config.active_primary_region)


def _s3_bak():
    return boto3.client("s3", region_name=config.active_backup_region)


def _lam():
    return boto3.client("lambda", region_name=config.lambda_region)


# ---- Full sync -------------------------------------------------------------

def _run_full_sync():
    s3_pri = _s3_pri()
    s3_bak = _s3_bak()

    def _list(s3c, bucket):
        objs = {}
        pag = s3c.get_paginator("list_objects_v2")
        for page in pag.paginate(Bucket=bucket):
            for o in page.get("Contents", []):
                objs[o["Key"]] = o["ETag"].strip('"')
        return objs

    primary = _list(s3_pri, config.primary_bucket)
    backup = _list(s3_bak, config.backup_bucket)

    to_copy = [k for k, etag in primary.items() if backup.get(k) != etag]
    synced, failed = 0, []

    progress = st.progress(0, text="Syncing…")
    total = len(to_copy) or 1
    for i, key in enumerate(to_copy):
        try:
            s3_bak.copy(
                CopySource={"Bucket": config.primary_bucket, "Key": key},
                Bucket=config.backup_bucket,
                Key=key,
                ExtraArgs={"ServerSideEncryption": "AES256"},
                SourceClient=s3_pri,
            )
            synced += 1
        except ClientError as e:
            failed.append(f"{key}: {e.response['Error']['Message']}")
        progress.progress((i + 1) / total, text=f"Syncing {i+1}/{len(to_copy)}")

    progress.empty()
    return synced, len(primary) - len(to_copy), failed


# ---- Health check ----------------------------------------------------------

def _run_health_check():
    lam = _lam()
    resp = lam.invoke(
        FunctionName=config.healthchecker_lambda_name,
        InvocationType="RequestResponse",
    )
    payload = json.loads(resp["Payload"].read())
    return payload


# ---- Outage simulation -----------------------------------------------------

def _get_current_notification_config():
    s3 = _s3_pri()
    try:
        resp = s3.get_bucket_notification_configuration(Bucket=config.primary_bucket)
        resp.pop("ResponseMetadata", None)
        return resp
    except ClientError:
        return {}


def _disable_lambda_trigger():
    """Remove the S3 event notification — Lambda stops receiving events."""
    s3 = _s3_pri()
    config_backup = _get_current_notification_config()
    s3.put_bucket_notification_configuration(
        Bucket=config.primary_bucket,
        NotificationConfiguration={},
    )
    return config_backup


def _restore_lambda_trigger(saved_config: dict):
    """Re-apply the saved notification config."""
    s3 = _s3_pri()
    s3.put_bucket_notification_configuration(
        Bucket=config.primary_bucket,
        NotificationConfiguration=saved_config,
    )


# ---- Seed data -------------------------------------------------------------

def _run_seed():
    from cli.commands.seed import build_file_list
    from cli.utils import human_size
    s3 = _s3_pri()
    files = build_file_list()
    uploaded = []
    progress = st.progress(0, text="Seeding…")
    for i, (key, content) in enumerate(files):
        if len(content) > config.MAX_FILE_BYTES:
            content = content[:config.MAX_FILE_BYTES]
        s3.put_object(Bucket=config.primary_bucket, Key=key, Body=content)
        uploaded.append(f"{key} ({human_size(len(content))})")
        progress.progress((i + 1) / len(files), text=f"Uploading {i+1}/{len(files)}")
    progress.empty()
    return uploaded


# ---- Page render -----------------------------------------------------------

def render():
    st.title("⚙️ Controls")

    # Outage state stored in session
    if "outage_active" not in st.session_state:
        st.session_state.outage_active = False
    if "saved_notification_config" not in st.session_state:
        st.session_state.saved_notification_config = {}

    # Notification area
    notif_area = st.empty()

    # ---- Row 1: Sync + Health Check ----------------------------------------
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("🔄 Full Sync")
        st.caption("Force primary → backup sync for all objects.")
        if st.button("Trigger Full Sync", use_container_width=True):
            with st.spinner("Running full sync…"):
                synced, already, failed = _run_full_sync()
            if failed:
                notif_area.error(
                    f"Sync finished with errors: {synced} synced, {already} already in sync, "
                    f"{len(failed)} failed.\n" + "\n".join(failed[:5])
                )
            else:
                notif_area.success(
                    f"Sync complete: **{synced}** synced, **{already}** already in sync."
                )

    with col2:
        st.subheader("🩺 Health Check")
        st.caption("Invoke the HealthChecker Lambda and see results.")
        if st.button("Run Health Check", use_container_width=True):
            with st.spinner("Invoking HealthChecker Lambda…"):
                try:
                    result = _run_health_check()
                    status = result.get("status", "UNKNOWN")
                    sync_pct = result.get("sync_percentage", 0)
                    if status == "HEALTHY":
                        notif_area.success(
                            f"Health check: **{status}** — {result['primary_count']} objects, "
                            f"{sync_pct}% in sync."
                        )
                    else:
                        missing = result.get("missing_in_backup", 0)
                        mismatched = result.get("mismatched", 0)
                        notif_area.warning(
                            f"Health check: **{status}** — {sync_pct}% in sync. "
                            f"Missing in backup: {missing}. Mismatches: {mismatched}."
                        )
                    with st.expander("Full health check result"):
                        st.json(result)
                except ClientError as e:
                    notif_area.error(f"Failed to invoke Lambda: {e.response['Error']['Message']}")

    st.divider()

    # ---- Row 2: Outage simulation + Seed -----------------------------------
    col3, col4 = st.columns(2)

    with col3:
        st.subheader("⚡ Simulate Outage")
        if not st.session_state.outage_active:
            st.caption(
                "Disables the S3 → Replicator Lambda trigger so new uploads to primary "
                "are NOT replicated. Backup remains independently accessible."
            )
            if st.button("🔴 Start Outage Simulation", use_container_width=True, type="primary"):
                with st.spinner("Disabling Lambda trigger…"):
                    saved = _disable_lambda_trigger()
                    st.session_state.saved_notification_config = saved
                    st.session_state.outage_active = True
                notif_area.warning(
                    "⚠️ **OUTAGE SIMULATED** — Lambda trigger disabled. "
                    "New uploads to the primary bucket will NOT be replicated until you end the simulation. "
                    "The backup bucket remains accessible and independent."
                )
                st.rerun()
        else:
            st.caption("Outage simulation is **active**. Lambda trigger is disabled.")
            st.warning("🔴 OUTAGE ACTIVE — replication is paused")
            if st.button("🟢 End Outage Simulation", use_container_width=True):
                with st.spinner("Restoring Lambda trigger…"):
                    _restore_lambda_trigger(st.session_state.saved_notification_config)
                    st.session_state.outage_active = False
                    st.session_state.saved_notification_config = {}
                notif_area.success(
                    "✅ Outage simulation ended — Lambda trigger restored. Replication is active again."
                )
                st.rerun()

    with col4:
        st.subheader("🌱 Seed Test Data")
        st.caption("Generate and upload ~20 fake NGO files to the primary bucket.")
        if st.button("Seed Test Data", use_container_width=True):
            with st.spinner("Generating and uploading fake NGO data…"):
                try:
                    uploaded = _run_seed()
                    notif_area.success(f"Seeded {len(uploaded)} files to `{config.primary_bucket}`.")
                    with st.expander("Uploaded files"):
                        for f in uploaded:
                            st.markdown(f"- `{f}`")
                except Exception as e:
                    notif_area.error(f"Seed failed: {e}")
