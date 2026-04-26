import csv
import io
import json
import os
import sys
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli.commands.seed import build_file_list
from cli.utils import human_size
from infra.config import config


app = FastAPI(title="AWS-RESCUE API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_state_lock = threading.Lock()
_runtime_state: dict[str, Any] = {
    "outage_active": False,
    "saved_notification_config": {},
}


def _s3(region: str):
    return boto3.client("s3", region_name=region)


def _primary_s3():
    return _s3(config.active_primary_region)


def _backup_s3():
    return _s3(config.active_backup_region)


def _lambda_client():
    return boto3.client("lambda", region_name=config.lambda_region)


def _table():
    dynamo = boto3.resource("dynamodb", region_name=config.dynamo_region)
    return dynamo.Table(config.dynamo_table)


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _scan_logs(limit: int = 1000) -> list[dict[str, Any]]:
    table = _table()
    items: list[dict[str, Any]] = []
    last_key = None

    while len(items) < limit:
        chunk = min(200, limit - len(items))
        kwargs: dict[str, Any] = {"Limit": chunk}
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key

        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")

        if not last_key:
            break

    return items


def _normalize_log_item(item: dict[str, Any]) -> dict[str, Any]:
    try:
        size_value = int(item.get("size_bytes", 0) or 0)
    except (TypeError, ValueError):
        size_value = 0

    return {
        "object_key": item.get("object_key", ""),
        "timestamp": item.get("timestamp", ""),
        "status": item.get("status", ""),
        "size_bytes": size_value,
        "source_region": item.get("source_region", ""),
        "dest_region": item.get("dest_region", ""),
        "checksum_sha256": item.get("checksum_sha256", ""),
        "error_message": item.get("error_message", ""),
    }


def _list_bucket(bucket: str, region: str) -> tuple[list[dict[str, Any]], bool, float | None]:
    s3 = _s3(region)
    objects: list[dict[str, Any]] = []
    healthy = True
    latency_ms: float | None = None

    try:
        t0 = time.perf_counter()
        s3.head_bucket(Bucket=bucket)
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)

        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            for obj in page.get("Contents", []):
                objects.append(
                    {
                        "key": obj["Key"],
                        "size_bytes": obj["Size"],
                        "size_hr": human_size(obj["Size"]),
                        "last_modified": obj["LastModified"].strftime("%Y-%m-%d %H:%M:%S"),
                        "etag": obj["ETag"].strip('"'),
                    }
                )
    except ClientError:
        healthy = False

    objects.sort(key=lambda x: x["key"])
    return objects, healthy, latency_ms


def _filter_logs(
    logs: list[dict[str, Any]],
    status: str = "ALL",
    from_date: date | None = None,
    key_query: str = "",
) -> list[dict[str, Any]]:
    normalized_query = key_query.strip().lower()

    filtered: list[dict[str, Any]] = []
    for entry in logs:
        if status != "ALL" and entry.get("status") != status:
            continue

        entry_dt = _parse_ts(entry.get("timestamp"))
        if from_date and (entry_dt is None or entry_dt.date() < from_date):
            continue

        if normalized_query and normalized_query not in entry.get("object_key", "").lower():
            continue

        filtered.append(entry)

    filtered.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return filtered


def _run_full_sync() -> dict[str, Any]:
    s3_pri = _primary_s3()
    s3_bak = _backup_s3()

    def _list_objects(s3_client, bucket: str) -> dict[str, tuple[str, int]]:
        objects: dict[str, tuple[str, int]] = {}
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            for obj in page.get("Contents", []):
                objects[obj["Key"]] = (obj["ETag"].strip('"'), obj["Size"])
        return objects

    primary_objects = _list_objects(s3_pri, config.primary_bucket)
    backup_objects = _list_objects(s3_bak, config.backup_bucket)

    to_sync: list[tuple[str, int]] = []
    for key, (etag, size) in primary_objects.items():
        backup_entry = backup_objects.get(key)
        if backup_entry is None or backup_entry[0] != etag:
            to_sync.append((key, size))

    failed: list[dict[str, str]] = []
    synced = 0

    for key, _size in to_sync:
        try:
            s3_bak.copy(
                CopySource={"Bucket": config.primary_bucket, "Key": key},
                Bucket=config.backup_bucket,
                Key=key,
                ExtraArgs={"ServerSideEncryption": "AES256"},
                SourceClient=s3_pri,
            )
            synced += 1
        except ClientError as error:
            failed.append(
                {
                    "key": key,
                    "error": error.response.get("Error", {}).get("Message", "Unknown copy error"),
                }
            )

    return {
        "synced": synced,
        "already_in_sync": len(primary_objects) - len(to_sync),
        "failed": len(failed),
        "failed_items": failed,
        "total_primary": len(primary_objects),
        "total_backup": len(backup_objects),
    }


def _run_health_check() -> dict[str, Any]:
    lambda_client = _lambda_client()
    resp = lambda_client.invoke(
        FunctionName=config.healthchecker_lambda_name,
        InvocationType="RequestResponse",
    )

    payload_bytes = resp.get("Payload").read() if resp.get("Payload") else b"{}"

    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError:
        payload = {"raw": payload_bytes.decode("utf-8", errors="replace")}

    return {
        "status_code": resp.get("StatusCode"),
        "function_error": resp.get("FunctionError"),
        "payload": payload,
    }


def _get_bucket_notification_config() -> dict[str, Any]:
    s3 = _primary_s3()
    resp = s3.get_bucket_notification_configuration(Bucket=config.primary_bucket)
    resp.pop("ResponseMetadata", None)
    return resp


def _start_outage_simulation() -> dict[str, Any]:
    with _state_lock:
        if _runtime_state["outage_active"]:
            return {"outage_active": True, "message": "Outage simulation already active."}

        saved_config = _get_bucket_notification_config()

        s3 = _primary_s3()
        s3.put_bucket_notification_configuration(
            Bucket=config.primary_bucket,
            NotificationConfiguration={},
        )

        _runtime_state["saved_notification_config"] = saved_config
        _runtime_state["outage_active"] = True

    return {
        "outage_active": True,
        "message": "Outage simulation started. Lambda trigger disabled.",
    }


def _end_outage_simulation() -> dict[str, Any]:
    with _state_lock:
        if not _runtime_state["outage_active"]:
            return {"outage_active": False, "message": "Outage simulation is not active."}

        saved_config = _runtime_state.get("saved_notification_config", {})

        s3 = _primary_s3()
        s3.put_bucket_notification_configuration(
            Bucket=config.primary_bucket,
            NotificationConfiguration=saved_config,
        )

        _runtime_state["saved_notification_config"] = {}
        _runtime_state["outage_active"] = False

    return {
        "outage_active": False,
        "message": "Outage simulation ended. Lambda trigger restored.",
    }


def _run_seed() -> list[dict[str, Any]]:
    s3 = _primary_s3()
    files = build_file_list()

    uploaded: list[dict[str, Any]] = []
    for s3_key, content in files:
        content_bytes = content
        if len(content_bytes) > config.MAX_FILE_BYTES:
            content_bytes = content_bytes[: config.MAX_FILE_BYTES]

        s3.put_object(Bucket=config.primary_bucket, Key=s3_key, Body=content_bytes)
        uploaded.append(
            {
                "key": s3_key,
                "size_bytes": len(content_bytes),
                "size_hr": human_size(len(content_bytes)),
            }
        )

    return uploaded


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "timestamp": _iso_utc_now()}


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return {
        "project_id": config.project_id,
        "primary_bucket": config.primary_bucket,
        "backup_bucket": config.backup_bucket,
        "active_primary_region": config.active_primary_region,
        "active_backup_region": config.active_backup_region,
        "dynamo_table": config.dynamo_table,
        "failover_state": "FAILED_OVER" if not config._primary_is_original else "NORMAL",
    }


@app.get("/api/overview")
def get_overview() -> dict[str, Any]:
    try:
        primary_objects, primary_ok, primary_latency = _list_bucket(
            config.primary_bucket,
            config.active_primary_region,
        )
        backup_objects, backup_ok, backup_latency = _list_bucket(
            config.backup_bucket,
            config.active_backup_region,
        )

        logs = [_normalize_log_item(item) for item in _scan_logs(1000)]

        success_logs = [log for log in logs if log["status"] == "SUCCESS"]
        success_logs.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        last_sync = success_logs[0]["timestamp"] if success_logs else "Never"

        active_alerts = sum(1 for log in logs if log.get("status") == "DRIFT_DETECTED")

        primary_count = len(primary_objects)
        backup_count = len(backup_objects)
        in_sync = min(primary_count, backup_count)
        sync_pct = round((in_sync / primary_count) * 100, 1) if primary_count > 0 else 0.0

        timeline_map: dict[str, int] = defaultdict(int)
        for log in success_logs:
            ts = _parse_ts(log.get("timestamp"))
            if ts is None:
                continue
            hour_key = ts.replace(minute=0, second=0, microsecond=0).isoformat()
            timeline_map[hour_key] += 1

        timeline = [
            {"hour": hour, "replications": count}
            for hour, count in sorted(timeline_map.items())
        ]

        drift_map: dict[tuple[str, str], int] = defaultdict(int)
        for log in logs:
            ts = _parse_ts(log.get("timestamp"))
            if ts is None:
                continue
            day_key = ts.date().isoformat()
            drift_map[(day_key, log.get("status", "UNKNOWN"))] += 1

        drift_history = [
            {"day": day, "status": status, "count": count}
            for (day, status), count in sorted(drift_map.items())
        ]

        return {
            "metrics": {
                "total_objects_primary": primary_count,
                "total_objects_backup": backup_count,
                "sync_percentage": sync_pct,
                "last_successful_sync": last_sync,
                "active_drift_alerts": active_alerts,
                "primary_size_bytes": sum(obj["size_bytes"] for obj in primary_objects),
                "backup_size_bytes": sum(obj["size_bytes"] for obj in backup_objects),
            },
            "regions": [
                {
                    "region": config.active_primary_region,
                    "role": "Primary",
                    "bucket": config.primary_bucket,
                    "health": "Healthy" if primary_ok else "Unavailable",
                    "latency_ms": primary_latency,
                },
                {
                    "region": config.active_backup_region,
                    "role": "Backup",
                    "bucket": config.backup_bucket,
                    "health": "Healthy" if backup_ok else "Unavailable",
                    "latency_ms": backup_latency,
                },
            ],
            "timeline": timeline,
            "drift_history": drift_history,
            "outage_active": _runtime_state["outage_active"],
            "failover_state": "FAILED_OVER" if not config._primary_is_original else "NORMAL",
        }
    except ClientError as error:
        raise HTTPException(status_code=500, detail=error.response.get("Error", {}).get("Message", "AWS error"))


@app.get("/api/objects")
def get_objects(search: str = Query(default="")) -> dict[str, Any]:
    try:
        primary_objects, _, _ = _list_bucket(config.primary_bucket, config.active_primary_region)
        backup_objects, _, _ = _list_bucket(config.backup_bucket, config.active_backup_region)

        backup_by_key = {obj["key"]: obj for obj in backup_objects}
        primary_by_key = {obj["key"]: obj for obj in primary_objects}

        primary_view: list[dict[str, Any]] = []
        for obj in primary_objects:
            backup_entry = backup_by_key.get(obj["key"])
            etag_match = bool(backup_entry and backup_entry.get("etag") == obj.get("etag"))
            primary_view.append(
                {
                    **obj,
                    "in_backup": etag_match,
                    "backup_etag": backup_entry.get("etag") if backup_entry else "",
                }
            )

        backup_view: list[dict[str, Any]] = []
        for obj in backup_objects:
            primary_entry = primary_by_key.get(obj["key"])
            etag_match = bool(primary_entry and primary_entry.get("etag") == obj.get("etag"))
            backup_view.append(
                {
                    **obj,
                    "matches_primary": etag_match,
                    "primary_etag": primary_entry.get("etag") if primary_entry else "",
                }
            )

        if search:
            q = search.lower().strip()
            primary_view = [obj for obj in primary_view if q in obj["key"].lower()]
            backup_view = [obj for obj in backup_view if q in obj["key"].lower()]

        return {
            "primary": primary_view,
            "backup": backup_view,
            "summary": {
                "primary_count": len(primary_view),
                "backup_count": len(backup_view),
            },
        }
    except ClientError as error:
        raise HTTPException(status_code=500, detail=error.response.get("Error", {}).get("Message", "AWS error"))


@app.get("/api/objects/{object_key:path}/history")
def get_object_history(
    object_key: str,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    table = _table()
    try:
        resp = table.query(
            KeyConditionExpression=Key("object_key").eq(object_key),
            ScanIndexForward=False,
            Limit=limit,
        )
        items = resp.get("Items", [])
    except Exception:
        # Fallback when query key schema is not available as expected.
        all_items = [_normalize_log_item(item) for item in _scan_logs(2000)]
        items = [item for item in all_items if item.get("object_key") == object_key][:limit]

    normalized = [_normalize_log_item(item) for item in items]
    normalized.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

    return {
        "object_key": object_key,
        "history": normalized,
    }


@app.get("/api/logs")
def get_logs(
    status: str = Query(default="ALL"),
    from_date: date | None = Query(default=None),
    key_query: str = Query(default=""),
    limit: int = Query(default=1000, ge=1, le=5000),
) -> dict[str, Any]:
    logs = [_normalize_log_item(item) for item in _scan_logs(limit)]
    filtered = _filter_logs(logs, status=status, from_date=from_date, key_query=key_query)

    failed_logs = [
        {
            "timestamp": entry["timestamp"],
            "object_key": entry["object_key"],
            "error_message": entry.get("error_message", "No error message"),
        }
        for entry in filtered
        if entry.get("status") == "FAILED"
    ]

    return {
        "count": len(filtered),
        "items": filtered,
        "failed_details": failed_logs,
    }


@app.get("/api/logs.csv")
def download_logs_csv(
    status: str = Query(default="ALL"),
    from_date: date | None = Query(default=None),
    key_query: str = Query(default=""),
    limit: int = Query(default=1000, ge=1, le=5000),
):
    logs = [_normalize_log_item(item) for item in _scan_logs(limit)]
    filtered = _filter_logs(logs, status=status, from_date=from_date, key_query=key_query)

    buf = io.StringIO()
    fieldnames = [
        "timestamp",
        "object_key",
        "status",
        "size_bytes",
        "source_region",
        "dest_region",
        "checksum_sha256",
        "error_message",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(filtered)

    data = io.BytesIO(buf.getvalue().encode("utf-8"))
    filename = f"rescue_logs_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"

    return StreamingResponse(
        data,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/outage-state")
def get_outage_state() -> dict[str, Any]:
    return {"outage_active": _runtime_state["outage_active"]}


@app.post("/api/actions/full-sync")
def action_full_sync() -> dict[str, Any]:
    try:
        result = _run_full_sync()
        return {"ok": True, "result": result}
    except ClientError as error:
        raise HTTPException(status_code=500, detail=error.response.get("Error", {}).get("Message", "Sync failed"))


@app.post("/api/actions/health-check")
def action_health_check() -> dict[str, Any]:
    try:
        result = _run_health_check()
        return {"ok": True, "result": result}
    except ClientError as error:
        raise HTTPException(status_code=500, detail=error.response.get("Error", {}).get("Message", "Health check failed"))


@app.post("/api/actions/outage/start")
def action_outage_start() -> dict[str, Any]:
    try:
        result = _start_outage_simulation()
        return {"ok": True, "result": result}
    except ClientError as error:
        raise HTTPException(status_code=500, detail=error.response.get("Error", {}).get("Message", "Outage start failed"))


@app.post("/api/actions/outage/end")
def action_outage_end() -> dict[str, Any]:
    try:
        result = _end_outage_simulation()
        return {"ok": True, "result": result}
    except ClientError as error:
        raise HTTPException(status_code=500, detail=error.response.get("Error", {}).get("Message", "Outage end failed"))


@app.post("/api/actions/seed")
def action_seed() -> dict[str, Any]:
    try:
        uploaded = _run_seed()
        return {
            "ok": True,
            "result": {
                "uploaded_count": len(uploaded),
                "uploaded": uploaded,
            },
        }
    except ClientError as error:
        raise HTTPException(status_code=500, detail=error.response.get("Error", {}).get("Message", "Seed failed"))
