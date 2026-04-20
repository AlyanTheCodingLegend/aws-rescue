import os
import sys
import click

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from infra.config import config
from cli.utils import primary_s3, human_size


@click.command()
@click.argument("file_path", type=click.Path(exists=True, readable=True))
@click.option("--prefix", default="", help="S3 key prefix (e.g. 'reports/')")
def upload(file_path: str, prefix: str):
    """Upload a local file to the primary S3 bucket."""
    file_name = os.path.basename(file_path)
    s3_key = f"{prefix.rstrip('/')}/{file_name}".lstrip("/") if prefix else file_name
    file_size = os.path.getsize(file_path)

    if file_size > config.MAX_FILE_BYTES * 1000:
        click.echo(
            f"WARNING: file size {human_size(file_size)} is large — ensure you stay within Free Tier limits."
        )

    s3 = primary_s3()
    click.echo(f"Uploading {file_name} ({human_size(file_size)}) -> s3://{config.primary_bucket}/{s3_key} ...")

    with open(file_path, "rb") as f:
        s3.put_object(
            Bucket=config.primary_bucket,
            Key=s3_key,
            Body=f,
        )

    click.echo(f"Done. S3 URI: s3://{config.primary_bucket}/{s3_key}")
    click.echo("The Replicator Lambda will replicate this to the backup bucket automatically.")
