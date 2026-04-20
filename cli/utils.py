import os
import sys
import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from infra.config import config


def s3_client(region: str):
    return boto3.client("s3", region_name=region)


def primary_s3():
    return s3_client(config.active_primary_region)


def backup_s3():
    return s3_client(config.active_backup_region)


def dynamo_table():
    dynamo = boto3.resource("dynamodb", region_name=config.dynamo_region)
    return dynamo.Table(config.dynamo_table)


def lambda_client():
    return boto3.client("lambda", region_name=config.lambda_region)


def human_size(num_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def check_free_tier_warning(s3_primary, s3_backup):
    """Warn if total bucket usage is approaching the free tier limit."""
    total = 0
    for bucket, region in [
        (config.primary_bucket, config.active_primary_region),
        (config.backup_bucket, config.active_backup_region),
    ]:
        try:
            paginator = s3_primary.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket):
                for obj in page.get("Contents", []):
                    total += obj["Size"]
        except ClientError:
            pass

    if total >= config.FREE_TIER_WARN_BYTES:
        from rich.console import Console
        Console().print(
            f"[bold yellow]WARNING:[/bold yellow] Total bucket usage ({human_size(total)}) "
            f"approaching AWS Free Tier limit (5 GB). Monitor closely."
        )
