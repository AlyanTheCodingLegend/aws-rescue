import os
import sys
import click
from rich.console import Console
from rich.table import Table
from rich import box

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from infra.config import config
from cli.utils import primary_s3, backup_s3, human_size


def _list_objects(s3, bucket: str) -> dict:
    objects = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            objects[obj["Key"]] = (obj["ETag"].strip('"'), obj["Size"])
    return objects


@click.command()
def drift():
    """Run drift detection: compare primary vs backup inventories."""
    console = Console()

    console.print(f"[bold]Scanning primary bucket:[/bold] {config.primary_bucket} ({config.active_primary_region})")
    s3_pri = primary_s3()
    primary_objects = _list_objects(s3_pri, config.primary_bucket)

    console.print(f"[bold]Scanning backup bucket:[/bold]  {config.backup_bucket} ({config.active_backup_region})")
    s3_bak = backup_s3()
    backup_objects = _list_objects(s3_bak, config.backup_bucket)

    primary_keys = set(primary_objects.keys())
    backup_keys = set(backup_objects.keys())

    missing_in_backup = sorted(primary_keys - backup_keys)
    missing_in_primary = sorted(backup_keys - primary_keys)
    mismatched = sorted(
        k for k in primary_keys & backup_keys
        if primary_objects[k] != backup_objects[k]
    )
    in_sync_count = len(primary_keys & backup_keys) - len(mismatched)
    total = len(primary_keys)
    sync_pct = (in_sync_count / total * 100) if total > 0 else 100.0

    # Summary header
    status_color = "green" if not (missing_in_backup or missing_in_primary or mismatched) else "red"
    status_label = "HEALTHY" if status_color == "green" else "DRIFT DETECTED"

    console.print()
    console.rule(f"[{status_color} bold]Drift Report | {status_label}[/{status_color} bold]")
    console.print(f"  Primary objects : [bold]{total}[/bold]")
    console.print(f"  Backup objects  : [bold]{len(backup_keys)}[/bold]")
    console.print(f"  In sync         : [green]{in_sync_count}[/green]")
    console.print(f"  Sync percentage : [{status_color}]{sync_pct:.1f}%[/{status_color}]")
    console.print()

    if missing_in_backup:
        t = Table(title=f"Missing in Backup ({len(missing_in_backup)})", box=box.SIMPLE, show_header=True)
        t.add_column("Object Key", style="red")
        t.add_column("Size", justify="right")
        for k in missing_in_backup:
            etag, size = primary_objects[k]
            t.add_row(k, human_size(size))
        console.print(t)

    if missing_in_primary:
        t = Table(title=f"Orphans in Backup ({len(missing_in_primary)})", box=box.SIMPLE, show_header=True)
        t.add_column("Object Key", style="yellow")
        t.add_column("Backup Size", justify="right")
        for k in missing_in_primary:
            etag, size = backup_objects[k]
            t.add_row(k, human_size(size))
        console.print(t)

    if mismatched:
        t = Table(title=f"ETag/Size Mismatches ({len(mismatched)})", box=box.SIMPLE, show_header=True)
        t.add_column("Object Key", style="yellow")
        t.add_column("Primary ETag")
        t.add_column("Backup ETag")
        t.add_column("Primary Size", justify="right")
        t.add_column("Backup Size", justify="right")
        for k in mismatched:
            pe, ps = primary_objects[k]
            be, bs = backup_objects[k]
            t.add_row(k, pe[:16] + "...", be[:16] + "...", human_size(ps), human_size(bs))
        console.print(t)

    if not (missing_in_backup or missing_in_primary or mismatched):
        console.print("[green bold]All objects are in sync. No drift detected.[/green bold]")
