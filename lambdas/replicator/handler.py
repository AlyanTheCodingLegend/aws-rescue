"""
Lambda: Replicator
Triggered by S3 ObjectCreated/ObjectRemoved events on the primary bucket.
Copies objects cross-region to the backup bucket and logs to DynamoDB.
"""
import os
import json
import hashlib
import logging
from datetime import datetime, timezone
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DEST_BUCKET = os.environ["DEST_BUCKET"]
DEST_REGION = os.environ["DEST_REGION"]
SOURCE_REGION = os.environ["SOURCE_REGION"]
DYNAMO_TABLE = os.environ["DYNAMO_TABLE"]
DYNAMO_REGION = os.environ["DYNAMO_REGION"]

s3_source = boto3.client("s3", region_name=SOURCE_REGION)
s3_dest = boto3.client("s3", region_name=DEST_REGION)
dynamo = boto3.resource("dynamodb", region_name=DYNAMO_REGION)
table = dynamo.Table(DYNAMO_TABLE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _ttl_30_days() -> int:
    from datetime import timedelta
    dt = datetime.now(timezone.utc) + timedelta(days=30)
    return int(dt.timestamp())


def _log_dynamo(object_key: str, status: str, size_bytes: int = 0,
                checksum: str = "", source_region: str = SOURCE_REGION,
                dest_region: str = DEST_REGION, error_message: str = ""):
    item = {
        "object_key": object_key,
        "timestamp": _now_iso(),
        "status": status,
        "size_bytes": size_bytes,
        "checksum_sha256": checksum,
        "source_region": source_region,
        "dest_region": dest_region,
        "expiry_ttl": _ttl_30_days(),
    }
    if error_message:
        item["error_message"] = error_message
    try:
        table.put_item(Item=item)
    except Exception as e:
        logger.error(f"DynamoDB log failed for {object_key}: {e}")


def _handle_created(source_bucket: str, object_key: str, size_bytes: int):
    logger.info(f"Replicating: s3://{source_bucket}/{object_key} -> s3://{DEST_BUCKET}/{object_key}")

    # Use server-side copy (never streams object body through Lambda memory)
    copy_source = {"Bucket": source_bucket, "Key": object_key}
    try:
        s3_dest.copy(
            CopySource=copy_source,
            Bucket=DEST_BUCKET,
            Key=object_key,
            ExtraArgs={"ServerSideEncryption": "AES256"},
            SourceClient=s3_source,
        )
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            logger.warning(f"Object vanished before copy: {object_key}")
            return
        raise

    # Get checksum from ETag of the destination object (server computed)
    head = s3_dest.head_object(Bucket=DEST_BUCKET, Key=object_key)
    etag = head.get("ETag", "").strip('"')

    _log_dynamo(
        object_key=object_key,
        status="SUCCESS",
        size_bytes=size_bytes,
        checksum=etag,
        source_region=SOURCE_REGION,
        dest_region=DEST_REGION,
    )
    logger.info(f"Replicated OK: {object_key}  size={size_bytes}  etag={etag}")


def _handle_removed(source_bucket: str, object_key: str):
    logger.info(f"Deleting from backup: s3://{DEST_BUCKET}/{object_key}")
    try:
        s3_dest.delete_object(Bucket=DEST_BUCKET, Key=object_key)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            logger.info(f"Object already absent in backup: {object_key}")
            return
        raise
    _log_dynamo(object_key=object_key, status="DELETED")
    logger.info(f"Deleted from backup: {object_key}")


def lambda_handler(event, context):
    records = event.get("Records", [])
    results = {"processed": 0, "failed": 0}

    for record in records:
        event_name = record.get("eventName", "")
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"].get("key", ""))
        size = record["s3"]["object"].get("size", 0)

        try:
            if event_name.startswith("ObjectCreated"):
                _handle_created(bucket, key, size)
            elif event_name.startswith("ObjectRemoved"):
                _handle_removed(bucket, key)
            else:
                logger.info(f"Ignored event: {event_name} for {key}")
            results["processed"] += 1
        except Exception as e:
            logger.error(f"Failed to process {key}: {e}")
            _log_dynamo(
                object_key=key,
                status="FAILED",
                size_bytes=size,
                error_message=str(e),
            )
            results["failed"] += 1
            # Re-raise on last record so Lambda retries the batch
            if record is records[-1]:
                raise

    logger.info(f"Done. processed={results['processed']} failed={results['failed']}")
    return results
