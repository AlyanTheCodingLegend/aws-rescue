import csv
import io
import json
import os
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
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
}

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB hard cap on user uploads


# ---------------------------------------------------------------------------
# Per-request credentials (from HTTP headers; never persisted server-side)
# ---------------------------------------------------------------------------

@dataclass
class RequestCreds:
    access_key_id: str = ""
    secret_access_key: str = ""
    region: str = ""
    project_id: str = ""

    def _boto3_kwargs(self) -> dict:
        if self.access_key_id and self.secret_access_key:
            return {
                "aws_access_key_id": self.access_key_id,
                "aws_secret_access_key": self.secret_access_key,
            }
        return {}

    def _primary_region(self) -> str:
        return self.region or config.primary_region

    def eff_project_id(self) -> str:
        return self.project_id or config.project_id

    def eff_active_primary_region(self) -> str:
        p = self._primary_region()
        return p if config._effective_original else config.backup_region

    def eff_active_backup_region(self) -> str:
        p = self._primary_region()
        return config.backup_region if config._effective_original else p

    def eff_dynamo_region(self) -> str:
        return self.region or config.dynamo_region

    def eff_lambda_region(self) -> str:
        return self.region or config.lambda_region

    def eff_primary_bucket(self) -> str:
        pid = self.eff_project_id()
        return f"rescue-primary-{pid}" if config._effective_original else f"rescue-backup-{pid}"

    def eff_backup_bucket(self) -> str:
        pid = self.eff_project_id()
        return f"rescue-backup-{pid}" if config._effective_original else f"rescue-primary-{pid}"


def get_creds(
    x_aws_access_key_id: str | None = Header(default=None),
    x_aws_secret_access_key: str | None = Header(default=None),
    x_aws_region: str | None = Header(default=None),
    x_project_id: str | None = Header(default=None),
) -> RequestCreds:
    return RequestCreds(
        access_key_id=x_aws_access_key_id or "",
        secret_access_key=x_aws_secret_access_key or "",
        region=x_aws_region or "",
        project_id=x_project_id or "",
    )


# ---------------------------------------------------------------------------
# AWS client helpers
# ---------------------------------------------------------------------------

def _s3(region: str, creds: RequestCreds):
    return boto3.client("s3", region_name=region, **creds._boto3_kwargs())


def _primary_s3(creds: RequestCreds):
    return _s3(creds.eff_active_primary_region(), creds)


def _backup_s3(creds: RequestCreds):
    return _s3(creds.eff_active_backup_region(), creds)


def _lambda_client(creds: RequestCreds):
    return boto3.client("lambda", region_name=creds.eff_lambda_region(), **creds._boto3_kwargs())


def _table(creds: RequestCreds):
    dynamo = boto3.resource("dynamodb", region_name=creds.eff_dynamo_region(), **creds._boto3_kwargs())
    return dynamo.Table(config.dynamo_table)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _scan_logs(creds: RequestCreds, limit: int = 1000) -> list[dict[str, Any]]:
    table = _table(creds)
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


def _list_bucket(bucket: str, region: str, creds: RequestCreds) -> tuple[list[dict[str, Any]], bool, float | None]:
    s3 = _s3(region, creds)
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


def _run_full_sync(creds: RequestCreds) -> dict[str, Any]:
    s3_pri = _primary_s3(creds)
    s3_bak = _backup_s3(creds)

    primary_bucket = creds.eff_primary_bucket()
    backup_bucket = creds.eff_backup_bucket()

    def _list_objects(s3_client, bucket: str) -> dict[str, tuple[str, int]]:
        objects: dict[str, tuple[str, int]] = {}
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            for obj in page.get("Contents", []):
                objects[obj["Key"]] = (obj["ETag"].strip('"'), obj["Size"])
        return objects

    primary_objects = _list_objects(s3_pri, primary_bucket)
    backup_objects = _list_objects(s3_bak, backup_bucket)

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
                CopySource={"Bucket": primary_bucket, "Key": key},
                Bucket=backup_bucket,
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


def _run_health_check(creds: RequestCreds) -> dict[str, Any]:
    lambda_client = _lambda_client(creds)
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


def _start_outage_simulation(creds: RequestCreds) -> dict[str, Any]:
    with _state_lock:
        if _runtime_state["outage_active"]:
            return {
                "outage_active": True,
                "active_primary_bucket": creds.eff_primary_bucket(),
                "active_primary_region": creds.eff_active_primary_region(),
                "active_backup_bucket": creds.eff_backup_bucket(),
                "active_backup_region": creds.eff_active_backup_region(),
                "message": "Outage simulation already active.",
            }

        config.outage_active = True
        _runtime_state["outage_active"] = True

    return {
        "outage_active": True,
        "active_primary_bucket": creds.eff_primary_bucket(),
        "active_primary_region": creds.eff_active_primary_region(),
        "active_backup_bucket": creds.eff_backup_bucket(),
        "active_backup_region": creds.eff_active_backup_region(),
        "message": (
            f"Outage simulation started. {creds.eff_primary_bucket()} "
            f"({creds.eff_active_primary_region()}) is now the active primary."
        ),
    }


def _end_outage_simulation(creds: RequestCreds) -> dict[str, Any]:
    with _state_lock:
        if not _runtime_state["outage_active"]:
            return {
                "outage_active": False,
                "active_primary_bucket": creds.eff_primary_bucket(),
                "active_primary_region": creds.eff_active_primary_region(),
                "active_backup_bucket": creds.eff_backup_bucket(),
                "active_backup_region": creds.eff_active_backup_region(),
                "message": "Outage simulation is not active.",
            }

        config.outage_active = False
        _runtime_state["outage_active"] = False

    return {
        "outage_active": False,
        "active_primary_bucket": creds.eff_primary_bucket(),
        "active_primary_region": creds.eff_active_primary_region(),
        "active_backup_bucket": creds.eff_backup_bucket(),
        "active_backup_region": creds.eff_active_backup_region(),
        "message": (
            f"Outage simulation ended. {creds.eff_primary_bucket()} "
            f"({creds.eff_active_primary_region()}) restored as primary."
        ),
    }


def _run_seed(creds: RequestCreds) -> list[dict[str, Any]]:
    s3 = _primary_s3(creds)
    files = build_file_list()

    uploaded: list[dict[str, Any]] = []
    for s3_key, content in files:
        content_bytes = content
        if len(content_bytes) > config.MAX_FILE_BYTES:
            content_bytes = content_bytes[: config.MAX_FILE_BYTES]

        s3.put_object(Bucket=creds.eff_primary_bucket(), Key=s3_key, Body=content_bytes)
        uploaded.append(
            {
                "key": s3_key,
                "size_bytes": len(content_bytes),
                "size_hr": human_size(len(content_bytes)),
            }
        )

    return uploaded


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "timestamp": _iso_utc_now()}


@app.get("/api/config")
def get_config(creds: RequestCreds = Depends(get_creds)) -> dict[str, Any]:
    return {
        "project_id": creds.eff_project_id(),
        "primary_bucket": creds.eff_primary_bucket(),
        "backup_bucket": creds.eff_backup_bucket(),
        "active_primary_region": creds.eff_active_primary_region(),
        "active_backup_region": creds.eff_active_backup_region(),
        "dynamo_table": config.dynamo_table,
        "failover_state": "FAILED_OVER" if not config._primary_is_original else "NORMAL",
    }


@app.get("/api/overview")
def get_overview(creds: RequestCreds = Depends(get_creds)) -> dict[str, Any]:
    try:
        primary_objects, primary_ok, primary_latency = _list_bucket(
            creds.eff_primary_bucket(),
            creds.eff_active_primary_region(),
            creds,
        )
        backup_objects, backup_ok, backup_latency = _list_bucket(
            creds.eff_backup_bucket(),
            creds.eff_active_backup_region(),
            creds,
        )

        logs = [_normalize_log_item(item) for item in _scan_logs(creds, 1000)]

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
                    "region": creds.eff_active_primary_region(),
                    "role": "Primary",
                    "bucket": creds.eff_primary_bucket(),
                    "health": "Healthy" if primary_ok else "Unavailable",
                    "latency_ms": primary_latency,
                },
                {
                    "region": creds.eff_active_backup_region(),
                    "role": "Backup",
                    "bucket": creds.eff_backup_bucket(),
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
def get_objects(
    search: str = Query(default=""),
    creds: RequestCreds = Depends(get_creds),
) -> dict[str, Any]:
    try:
        primary_objects, _, _ = _list_bucket(creds.eff_primary_bucket(), creds.eff_active_primary_region(), creds)
        backup_objects, _, _ = _list_bucket(creds.eff_backup_bucket(), creds.eff_active_backup_region(), creds)

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
    creds: RequestCreds = Depends(get_creds),
) -> dict[str, Any]:
    table = _table(creds)
    try:
        resp = table.query(
            KeyConditionExpression=Key("object_key").eq(object_key),
            ScanIndexForward=False,
            Limit=limit,
        )
        items = resp.get("Items", [])
    except Exception:
        all_items = [_normalize_log_item(item) for item in _scan_logs(creds, 2000)]
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
    creds: RequestCreds = Depends(get_creds),
) -> dict[str, Any]:
    logs = [_normalize_log_item(item) for item in _scan_logs(creds, limit)]
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
    creds: RequestCreds = Depends(get_creds),
):
    logs = [_normalize_log_item(item) for item in _scan_logs(creds, limit)]
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
def action_full_sync(creds: RequestCreds = Depends(get_creds)) -> dict[str, Any]:
    try:
        result = _run_full_sync(creds)
        return {"ok": True, "result": result}
    except ClientError as error:
        raise HTTPException(status_code=500, detail=error.response.get("Error", {}).get("Message", "Sync failed"))


@app.post("/api/actions/health-check")
def action_health_check(creds: RequestCreds = Depends(get_creds)) -> dict[str, Any]:
    try:
        result = _run_health_check(creds)
        return {"ok": True, "result": result}
    except ClientError as error:
        raise HTTPException(status_code=500, detail=error.response.get("Error", {}).get("Message", "Health check failed"))


@app.post("/api/actions/outage/start")
def action_outage_start(creds: RequestCreds = Depends(get_creds)) -> dict[str, Any]:
    try:
        result = _start_outage_simulation(creds)
        return {"ok": True, "result": result}
    except ClientError as error:
        raise HTTPException(status_code=500, detail=error.response.get("Error", {}).get("Message", "Outage start failed"))


@app.post("/api/actions/outage/end")
def action_outage_end(creds: RequestCreds = Depends(get_creds)) -> dict[str, Any]:
    try:
        result = _end_outage_simulation(creds)
        return {"ok": True, "result": result}
    except ClientError as error:
        raise HTTPException(status_code=500, detail=error.response.get("Error", {}).get("Message", "Outage end failed"))


@app.post("/api/actions/seed")
def action_seed(creds: RequestCreds = Depends(get_creds)) -> dict[str, Any]:
    try:
        uploaded = _run_seed(creds)
        return {
            "ok": True,
            "result": {
                "uploaded_count": len(uploaded),
                "uploaded": uploaded,
            },
        }
    except ClientError as error:
        raise HTTPException(status_code=500, detail=error.response.get("Error", {}).get("Message", "Seed failed"))


def _sanitize_prefix(prefix: str) -> str:
    cleaned = (prefix or "").strip().strip("/")
    parts = [p for p in cleaned.split("/") if p and p not in ("..", ".")]
    return "/".join(parts)


@app.post("/api/actions/upload-file")
async def action_upload_file(
    file: UploadFile = File(...),
    prefix: str = Form(default="uploads"),
    creds: RequestCreds = Depends(get_creds),
) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="A file is required.")

    content = await file.read()
    size = len(content)

    if size == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({human_size(size)}). Limit is {human_size(MAX_UPLOAD_BYTES)}.",
        )

    safe_name = os.path.basename(file.filename).replace("\\", "/").lstrip("/")
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename.")

    safe_prefix = _sanitize_prefix(prefix)
    s3_key = f"{safe_prefix}/{safe_name}" if safe_prefix else safe_name

    target_bucket = creds.eff_primary_bucket()
    target_region = creds.eff_active_primary_region()
    s3 = _s3(target_region, creds)

    try:
        s3.put_object(
            Bucket=target_bucket,
            Key=s3_key,
            Body=content,
            ContentType=file.content_type or "application/octet-stream",
            ServerSideEncryption="AES256",
        )
    except ClientError as error:
        raise HTTPException(
            status_code=500,
            detail=error.response.get("Error", {}).get("Message", "Upload failed"),
        )

    return {
        "ok": True,
        "result": {
            "key": s3_key,
            "size_bytes": size,
            "size_hr": human_size(size),
            "content_type": file.content_type or "application/octet-stream",
            "bucket": target_bucket,
            "region": target_region,
            "outage_active": _runtime_state["outage_active"],
        },
    }
