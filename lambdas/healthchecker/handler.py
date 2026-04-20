"""
Lambda: HealthChecker
Runs on a CloudWatch schedule (every 15 min).
Compares primary vs backup bucket inventories and logs drift to DynamoDB.
"""
import os
import json
import logging
from datetime import datetime, timezone, timedelta

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

PRIMARY_BUCKET = os.environ["PRIMARY_BUCKET"]
BACKUP_BUCKET = os.environ["BACKUP_BUCKET"]
PRIMARY_REGION = os.environ["PRIMARY_REGION"]
BACKUP_REGION = os.environ["BACKUP_REGION"]
DYNAMO_TABLE = os.environ["DYNAMO_TABLE"]
DYNAMO_REGION = os.environ["DYNAMO_REGION"]

s3_primary = boto3.client("s3", region_name=PRIMARY_REGION)
s3_backup = boto3.client("s3", region_name=BACKUP_REGION)
dynamo = boto3.resource("dynamodb", region_name=DYNAMO_REGION)
table = dynamo.Table(DYNAMO_TABLE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _ttl_30_days() -> int:
    dt = datetime.now(timezone.utc) + timedelta(days=30)
    return int(dt.timestamp())


def _list_bucket(s3_client, bucket: str) -> dict:
    """Returns {key: (etag, size)} for all objects in bucket."""
    objects = {}
    paginator = s3_client.get_paginator("list_objects_v2")
    try:
        for page in paginator.paginate(Bucket=bucket):
            for obj in page.get("Contents", []):
                objects[obj["Key"]] = (obj["ETag"].strip('"'), obj["Size"])
    except ClientError as e:
        logger.error(f"Failed to list bucket {bucket}: {e}")
        raise
    return objects


def _log_dynamo(object_key: str, status: str, error_message: str = ""):
    item = {
        "object_key": object_key,
        "timestamp": _now_iso(),
        "status": status,
        "source_region": PRIMARY_REGION,
        "dest_region": BACKUP_REGION,
        "size_bytes": 0,
        "checksum_sha256": "",
        "expiry_ttl": _ttl_30_days(),
    }
    if error_message:
        item["error_message"] = error_message
    try:
        table.put_item(Item=item)
    except Exception as e:
        logger.error(f"DynamoDB log failed: {e}")


def lambda_handler(event, context):
    timestamp = _now_iso()
    logger.info(f"Health check started at {timestamp}")

    primary_objects = _list_bucket(s3_primary, PRIMARY_BUCKET)
    backup_objects = _list_bucket(s3_backup, BACKUP_BUCKET)

    primary_keys = set(primary_objects.keys())
    backup_keys = set(backup_objects.keys())

    missing_in_backup = primary_keys - backup_keys
    missing_in_primary = backup_keys - primary_keys
    mismatched = {
        k for k in primary_keys & backup_keys
        if primary_objects[k] != backup_objects[k]
    }
    in_sync_count = len(primary_keys & backup_keys) - len(mismatched)

    total_primary = len(primary_keys)
    sync_pct = (in_sync_count / total_primary * 100) if total_primary > 0 else 100.0

    has_drift = bool(missing_in_backup or missing_in_primary or mismatched)

    if has_drift:
        drift_details = []
        for k in list(missing_in_backup)[:10]:
            drift_details.append(f"MISSING_IN_BACKUP: {k}")
            _log_dynamo(k, "DRIFT_DETECTED", "Missing in backup bucket")
        for k in list(missing_in_primary)[:10]:
            drift_details.append(f"ORPHAN_IN_BACKUP: {k}")
            _log_dynamo(k, "DRIFT_DETECTED", "Orphan in backup — missing from primary")
        for k in list(mismatched)[:10]:
            drift_details.append(f"ETAG_MISMATCH: {k}")
            _log_dynamo(k, "DRIFT_DETECTED", "ETag/size mismatch between primary and backup")

        alert_message = (
            f"AWS-RESCUE DRIFT DETECTED at {timestamp}\n"
            f"Primary: {total_primary} objects | Backup: {len(backup_keys)} objects\n"
            f"Missing in backup: {len(missing_in_backup)}\n"
            f"Orphans in backup: {len(missing_in_primary)}\n"
            f"ETag mismatches: {len(mismatched)}\n"
            f"Sync: {sync_pct:.1f}%\n\n"
            f"Affected keys (first 10 each):\n" + "\n".join(drift_details)
        )
        logger.warning(alert_message)
    else:
        _log_dynamo("__health_check__", "HEALTH_CHECK_OK")
        logger.info(f"Health check OK — {total_primary} objects in sync")

    result = {
        "timestamp": timestamp,
        "primary_count": total_primary,
        "backup_count": len(backup_keys),
        "in_sync": in_sync_count,
        "missing_in_backup": len(missing_in_backup),
        "missing_in_primary": len(missing_in_primary),
        "mismatched": len(mismatched),
        "sync_percentage": round(sync_pct, 2),
        "status": "DRIFT" if has_drift else "HEALTHY",
        "missing_in_backup_keys": list(missing_in_backup)[:10],
        "missing_in_primary_keys": list(missing_in_primary)[:10],
        "mismatched_keys": list(mismatched)[:10],
    }

    logger.info(json.dumps(result))
    return result
