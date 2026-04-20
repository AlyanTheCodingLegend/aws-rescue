"""
Failover command: swap primary and backup roles by rewriting infra/config.py.
Does NOT touch .env — the swap is encoded in the config source itself.
"""
import os
import sys
import re
import click
from rich.console import Console

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from infra.config import config

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "infra", "config.py",
)


def _read_config_source() -> str:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _write_config_source(source: str):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(source)


def _current_failover_state(source: str) -> bool:
    """Returns True if _primary_is_original is currently True."""
    m = re.search(r"_primary_is_original:\s*bool\s*=\s*(True|False)", source)
    if m:
        return m.group(1) == "True"
    return True


def _toggle_failover(source: str, new_value: bool) -> str:
    return re.sub(
        r"(_primary_is_original:\s*bool\s*=\s*)(True|False)",
        lambda m: m.group(1) + str(new_value),
        source,
    )


@click.command()
@click.option("--confirm", is_flag=True, help="Required to execute the failover (destructive).")
@click.option("--reverse-sync", is_flag=True, help="After failover, sync new-primary -> new-backup.")
def failover(confirm: bool, reverse_sync: bool):
    """Swap primary and backup bucket roles (rewrites infra/config.py)."""
    console = Console()

    source = _read_config_source()
    currently_original = _current_failover_state(source)

    if currently_original:
        new_primary = config.backup_bucket
        new_primary_region = config.backup_region
        new_backup = config.primary_bucket
        new_backup_region = config.primary_region
        action = "eu-west-1 (backup) -> PRIMARY | us-east-1 (original primary) -> BACKUP"
    else:
        new_primary = f"rescue-primary-{config.project_id}"
        new_primary_region = "us-east-1"
        new_backup = f"rescue-backup-{config.project_id}"
        new_backup_region = "eu-west-1"
        action = "Restoring original: us-east-1 -> PRIMARY | eu-west-1 -> BACKUP"

    console.print("\n[bold yellow]FAILOVER PLAN[/bold yellow]")
    console.print(f"  Current state : {'original' if currently_original else 'failed over'}")
    console.print(f"  Action        : {action}")
    console.print(f"  New primary   : [bold]{new_primary}[/bold] ({new_primary_region})")
    console.print(f"  New backup    : [bold]{new_backup}[/bold] ({new_backup_region})")
    console.print(f"  Config file   : {CONFIG_PATH}")

    if not confirm:
        console.print("\n[red]Aborted.[/red] Pass [bold]--confirm[/bold] to execute the failover.")
        return

    console.print("\n[bold]Executing failover...[/bold]")

    # Step 1: rewrite config.py
    new_source = _toggle_failover(source, not currently_original)
    _write_config_source(new_source)
    console.print(f"  [green]OK[/green] infra/config.py updated (_primary_is_original = {not currently_original})")

    # Step 2: verify backup bucket is accessible
    import boto3
    from botocore.exceptions import ClientError
    s3 = boto3.client("s3", region_name=new_primary_region)
    try:
        s3.head_bucket(Bucket=new_primary)
        console.print(f"  [green]OK[/green] New primary bucket accessible: {new_primary}")
    except ClientError as e:
        console.print(f"  [red]FAIL[/red] Cannot reach new primary bucket: {e.response['Error']['Message']}")
        console.print("  [yellow]Config has been updated but bucket may be unavailable.[/yellow]")

    # Step 3: optional reverse sync
    if reverse_sync:
        console.print("\n[bold]Running reverse sync (new primary -> new backup)...[/bold]")
        from cli.commands.sync import sync as sync_cmd
        from click.testing import CliRunner
        runner = CliRunner()
        result = runner.invoke(sync_cmd, [])
        console.print(result.output)

    console.print("\n[green bold]Failover complete.[/green bold]")
    console.print("  Restart the dashboard and CLI for changes to take effect.")
    console.print(f"  New primary : s3://{new_primary} ({new_primary_region})")
    console.print(f"  New backup  : s3://{new_backup} ({new_backup_region})")
