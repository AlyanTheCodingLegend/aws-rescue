import os
import sys
import click
from boto3.dynamodb.conditions import Key
from rich.console import Console
from rich.table import Table

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from infra.config import config
from cli.utils import dynamo_table, human_size

STATUS_COLORS = {
    "SUCCESS": "green",
    "FAILED": "red",
    "DELETED": "dim",
    "DRIFT_DETECTED": "yellow",
    "HEALTH_CHECK_OK": "cyan",
    "PENDING": "blue",
}


@click.command()
@click.option("--limit", default=20, show_default=True, help="Number of recent entries to show.")
def status(limit: int):
    """Show recent replication log entries from DynamoDB."""
    console = Console()
    table_ref = dynamo_table()

    resp = table_ref.scan(Limit=limit * 3)  # over-fetch then sort client-side
    items = resp.get("Items", [])
    items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    items = items[:limit]

    if not items:
        console.print("[yellow]No replication log entries found.[/yellow]")
        return

    table = Table(title=f"Replication Log (last {len(items)} entries)", show_lines=False)
    table.add_column("Timestamp", style="dim", min_width=24)
    table.add_column("Object Key", overflow="fold", min_width=30)
    table.add_column("Status", min_width=16)
    table.add_column("Size", justify="right", min_width=8)
    table.add_column("Route", min_width=20)

    for item in items:
        ts = item.get("timestamp", "—")[:23]
        key = item.get("object_key", "—")
        stat = item.get("status", "—")
        size = human_size(int(item.get("size_bytes", 0)))
        src = item.get("source_region", "")
        dst = item.get("dest_region", "")
        route = f"{src} -> {dst}" if src and dst else "-"

        color = STATUS_COLORS.get(stat, "white")
        table.add_row(ts, key, f"[{color}]{stat}[/{color}]", size, route)

    console.print(table)

    # Show error messages for failed items
    failed = [i for i in items if i.get("status") == "FAILED" and i.get("error_message")]
    if failed:
        console.print("\n[red bold]Errors:[/red bold]")
        for item in failed:
            console.print(f"  [dim]{item.get('timestamp', '')[:23]}[/dim]  {item.get('object_key')}")
            console.print(f"    [red]{item.get('error_message')}[/red]")
