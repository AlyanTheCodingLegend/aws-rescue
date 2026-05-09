import os
import sys
import click
from tqdm import tqdm
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from infra.config import config
from cli.utils import primary_s3, backup_s3, human_size


def _list_objects(s3, bucket: str) -> dict:
    """Returns {key: (etag, size)}."""
    objects = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            objects[obj["Key"]] = (obj["ETag"].strip('"'), obj["Size"])
    return objects


@click.command()
@click.option("--dry-run", is_flag=True, help="Report what would be synced without copying.")
def sync(dry_run: bool):
    """Force a full manual sync: primary -> backup."""
    s3_pri = primary_s3()
    s3_bak = backup_s3()

    click.echo(f"Listing objects in primary bucket ({config.primary_bucket})...")
    primary_objects = _list_objects(s3_pri, config.primary_bucket)
    click.echo(f"Listing objects in backup bucket ({config.backup_bucket})...")
    backup_objects = _list_objects(s3_bak, config.backup_bucket)

    to_sync = []
    already_synced = 0

    for key, (etag, size) in primary_objects.items():
        backup_entry = backup_objects.get(key)
        if backup_entry is None or backup_entry[0] != etag:
            to_sync.append((key, size))
        else:
            already_synced += 1

    if not to_sync:
        click.echo(f"All {already_synced} objects already in sync. Nothing to do.")
        return

    if dry_run:
        click.echo(f"\n[DRY RUN] Would sync {len(to_sync)} object(s):")
        for key, size in to_sync:
            click.echo(f"  {key}  ({human_size(size)})")
        click.echo(f"\n{already_synced} object(s) already in sync.")
        return

    synced = 0
    failed = 0
    copy_source_client = s3_pri

    with tqdm(total=len(to_sync), desc="Syncing", unit="obj") as pbar:
        for key, size in to_sync:
            try:
                s3_bak.copy(
                    CopySource={"Bucket": config.primary_bucket, "Key": key},
                    Bucket=config.backup_bucket,
                    Key=key,
                    ExtraArgs={"ServerSideEncryption": "AES256"},
                    SourceClient=copy_source_client,
                )
                synced += 1
            except ClientError as e:
                click.echo(f"\n  [FAILED] {key}: {e.response['Error']['Message']}")
                failed += 1
            pbar.update(1)

    click.echo(f"\nSync complete: {synced} synced | {already_synced} already in sync | {failed} failed")
