import os
import sys
import click
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli.commands.upload import upload
from cli.commands.sync import sync
from cli.commands.status import status
from cli.commands.drift import drift
from cli.commands.failover import failover
from cli.commands.seed import seed


@click.group()
def cli():
    """AWS-RESCUE: Cross-region S3 replication tool for NGO data survival."""
    pass


cli.add_command(seed)
cli.add_command(upload)
cli.add_command(sync)
cli.add_command(status)
cli.add_command(drift)
cli.add_command(failover)


@cli.command()
def dashboard():
    """Launch the Streamlit monitoring dashboard."""
    dashboard_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "dashboard", "app.py",
    )
    os.execvp("streamlit", ["streamlit", "run", dashboard_path])


if __name__ == "__main__":
    cli()
