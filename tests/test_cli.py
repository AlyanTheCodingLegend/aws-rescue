"""
Tests for CLI commands (cli/).
Uses Click's test runner + moto for all AWS calls.

Each test wraps itself in mock_aws() so that boto3 clients created inside
CLI commands (which are fresh imports) are also intercepted by moto.
"""
import os
import sys
import json
import tempfile
import pytest
import boto3
from moto import mock_aws
from click.testing import CliRunner

from tests.conftest import (
    PRIMARY_BUCKET, BACKUP_BUCKET, PRIMARY_REGION, BACKUP_REGION,
    DYNAMO_TABLE, DYNAMO_REGION,
)

import infra.config as _config_mod


def _reset_config():
    """Re-apply test config values -- must be called at the start of each test
    because test_infra.py (which runs first alphabetically) mutates the same
    shared config singleton with different project_id / table names."""
    _config_mod.config.project_id = "test"
    _config_mod.config.primary_region = PRIMARY_REGION
    _config_mod.config.backup_region = BACKUP_REGION
    _config_mod.config.dynamo_table = DYNAMO_TABLE
    _config_mod.config.dynamo_region = DYNAMO_REGION
    _config_mod.config._primary_is_original = True


def _bootstrap(s3_pri, s3_bak, dynamo):
    """Create buckets and DynamoDB table inside an already-active mock."""
    s3_pri.create_bucket(Bucket=PRIMARY_BUCKET)
    s3_bak.create_bucket(
        Bucket=BACKUP_BUCKET,
        CreateBucketConfiguration={"LocationConstraint": BACKUP_REGION},
    )
    dynamo.create_table(
        TableName=DYNAMO_TABLE,
        KeySchema=[
            {"AttributeName": "object_key", "KeyType": "HASH"},
            {"AttributeName": "timestamp", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "object_key", "AttributeType": "S"},
            {"AttributeName": "timestamp", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


# ---------------------------------------------------------------------------

class TestSeedCommand:
    def setup_method(self):
        _reset_config()

    @mock_aws
    def test_seed_uploads_expected_number_of_files(self):
        s3_pri = boto3.client("s3", region_name=PRIMARY_REGION)
        s3_bak = boto3.client("s3", region_name=BACKUP_REGION)
        dynamo = boto3.client("dynamodb", region_name=DYNAMO_REGION)
        _bootstrap(s3_pri, s3_bak, dynamo)

        from cli.commands.seed import seed
        runner = CliRunner()
        result = runner.invoke(seed, [])
        assert result.exit_code == 0, result.output

        paginator = s3_pri.get_paginator("list_objects_v2")
        keys = [o["Key"] for page in paginator.paginate(Bucket=PRIMARY_BUCKET)
                for o in page.get("Contents", [])]
        assert 15 <= len(keys) <= 22

    @mock_aws
    def test_seed_uses_correct_prefixes(self):
        s3_pri = boto3.client("s3", region_name=PRIMARY_REGION)
        s3_bak = boto3.client("s3", region_name=BACKUP_REGION)
        dynamo = boto3.client("dynamodb", region_name=DYNAMO_REGION)
        _bootstrap(s3_pri, s3_bak, dynamo)

        from cli.commands.seed import seed
        runner = CliRunner()
        runner.invoke(seed, [])

        paginator = s3_pri.get_paginator("list_objects_v2")
        keys = [o["Key"] for page in paginator.paginate(Bucket=PRIMARY_BUCKET)
                for o in page.get("Contents", [])]
        prefixes = {k.split("/")[0] for k in keys}
        assert prefixes <= {"donors", "reports", "projects", "finance"}

    @mock_aws
    def test_seed_files_under_size_limit(self):
        s3_pri = boto3.client("s3", region_name=PRIMARY_REGION)
        s3_bak = boto3.client("s3", region_name=BACKUP_REGION)
        dynamo = boto3.client("dynamodb", region_name=DYNAMO_REGION)
        _bootstrap(s3_pri, s3_bak, dynamo)

        from cli.commands.seed import seed
        runner = CliRunner()
        runner.invoke(seed, [])

        paginator = s3_pri.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=PRIMARY_BUCKET):
            for obj in page.get("Contents", []):
                assert obj["Size"] <= _config_mod.config.MAX_FILE_BYTES, (
                    f"{obj['Key']} exceeds 50 KB: {obj['Size']} bytes"
                )


class TestUploadCommand:
    def setup_method(self):
        _reset_config()

    @mock_aws
    def test_upload_puts_file_in_primary_bucket(self):
        s3_pri = boto3.client("s3", region_name=PRIMARY_REGION)
        s3_bak = boto3.client("s3", region_name=BACKUP_REGION)
        dynamo = boto3.client("dynamodb", region_name=DYNAMO_REGION)
        _bootstrap(s3_pri, s3_bak, dynamo)

        from cli.commands.upload import upload
        runner = CliRunner()

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="wb") as f:
            f.write(b"hello world")
            tmp_path = f.name

        try:
            result = runner.invoke(upload, [tmp_path])
            assert result.exit_code == 0, result.output

            key = os.path.basename(tmp_path)
            resp = s3_pri.get_object(Bucket=PRIMARY_BUCKET, Key=key)
            assert resp["Body"].read() == b"hello world"
        finally:
            os.unlink(tmp_path)

    @mock_aws
    def test_upload_respects_prefix(self):
        s3_pri = boto3.client("s3", region_name=PRIMARY_REGION)
        s3_bak = boto3.client("s3", region_name=BACKUP_REGION)
        dynamo = boto3.client("dynamodb", region_name=DYNAMO_REGION)
        _bootstrap(s3_pri, s3_bak, dynamo)

        from cli.commands.upload import upload
        runner = CliRunner()

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="wb") as f:
            f.write(b"col1,col2\n1,2")
            tmp_path = f.name

        try:
            result = runner.invoke(upload, [tmp_path, "--prefix", "finance/"])
            assert result.exit_code == 0, result.output

            key = f"finance/{os.path.basename(tmp_path)}"
            resp = s3_pri.get_object(Bucket=PRIMARY_BUCKET, Key=key)
            assert resp["Body"].read() == b"col1,col2\n1,2"
        finally:
            os.unlink(tmp_path)


class TestSyncCommand:
    def setup_method(self):
        _reset_config()

    @mock_aws
    def test_sync_copies_missing_objects_to_backup(self):
        s3_pri = boto3.client("s3", region_name=PRIMARY_REGION)
        s3_bak = boto3.client("s3", region_name=BACKUP_REGION)
        dynamo = boto3.client("dynamodb", region_name=DYNAMO_REGION)
        _bootstrap(s3_pri, s3_bak, dynamo)

        s3_pri.put_object(Bucket=PRIMARY_BUCKET, Key="donors/new.csv", Body=b"data")

        from cli.commands.sync import sync
        runner = CliRunner()
        result = runner.invoke(sync, [])
        assert result.exit_code == 0, result.output

        resp = s3_bak.get_object(Bucket=BACKUP_BUCKET, Key="donors/new.csv")
        assert resp["Body"].read() == b"data"

    @mock_aws
    def test_sync_dry_run_does_not_copy(self):
        s3_pri = boto3.client("s3", region_name=PRIMARY_REGION)
        s3_bak = boto3.client("s3", region_name=BACKUP_REGION)
        dynamo = boto3.client("dynamodb", region_name=DYNAMO_REGION)
        _bootstrap(s3_pri, s3_bak, dynamo)

        s3_pri.put_object(Bucket=PRIMARY_BUCKET, Key="reports/dry.txt", Body=b"content")

        from cli.commands.sync import sync
        runner = CliRunner()
        result = runner.invoke(sync, ["--dry-run"])
        assert result.exit_code == 0
        assert "DRY RUN" in result.output

        import botocore.exceptions
        with pytest.raises(botocore.exceptions.ClientError):
            s3_bak.head_object(Bucket=BACKUP_BUCKET, Key="reports/dry.txt")

    @mock_aws
    def test_sync_skips_already_synced_objects(self):
        s3_pri = boto3.client("s3", region_name=PRIMARY_REGION)
        s3_bak = boto3.client("s3", region_name=BACKUP_REGION)
        dynamo = boto3.client("dynamodb", region_name=DYNAMO_REGION)
        _bootstrap(s3_pri, s3_bak, dynamo)

        s3_pri.put_object(Bucket=PRIMARY_BUCKET, Key="finance/a.csv", Body=b"same")
        s3_bak.put_object(Bucket=BACKUP_BUCKET, Key="finance/a.csv", Body=b"same")

        from cli.commands.sync import sync
        runner = CliRunner()
        result = runner.invoke(sync, [])
        assert result.exit_code == 0
        assert "already in sync" in result.output


class TestStatusCommand:
    def setup_method(self):
        _reset_config()

    @mock_aws
    def test_status_shows_log_entries(self):
        s3_pri = boto3.client("s3", region_name=PRIMARY_REGION)
        s3_bak = boto3.client("s3", region_name=BACKUP_REGION)
        dynamo = boto3.client("dynamodb", region_name=DYNAMO_REGION)
        _bootstrap(s3_pri, s3_bak, dynamo)

        table = boto3.resource("dynamodb", region_name=DYNAMO_REGION).Table(DYNAMO_TABLE)
        table.put_item(Item={
            "object_key": "donors/file.csv",
            "timestamp": "2024-03-15T10:00:00.000Z",
            "status": "SUCCESS",
            "size_bytes": 1024,
            "source_region": PRIMARY_REGION,
            "dest_region": BACKUP_REGION,
            "checksum_sha256": "abc123",
        })

        from cli.commands.status import status
        runner = CliRunner()
        result = runner.invoke(status, ["--limit", "5"])
        assert result.exit_code == 0, result.output
        assert "donors/file.csv" in result.output
        assert "SUCCESS" in result.output

    @mock_aws
    def test_status_empty_table_shows_no_entries_message(self):
        s3_pri = boto3.client("s3", region_name=PRIMARY_REGION)
        s3_bak = boto3.client("s3", region_name=BACKUP_REGION)
        dynamo = boto3.client("dynamodb", region_name=DYNAMO_REGION)
        _bootstrap(s3_pri, s3_bak, dynamo)

        from cli.commands.status import status
        runner = CliRunner()
        result = runner.invoke(status, [])
        assert result.exit_code == 0
        assert "No replication log" in result.output


class TestDriftCommand:
    def setup_method(self):
        _reset_config()

    @mock_aws
    def test_drift_reports_healthy_when_in_sync(self):
        s3_pri = boto3.client("s3", region_name=PRIMARY_REGION)
        s3_bak = boto3.client("s3", region_name=BACKUP_REGION)
        dynamo = boto3.client("dynamodb", region_name=DYNAMO_REGION)
        _bootstrap(s3_pri, s3_bak, dynamo)

        s3_pri.put_object(Bucket=PRIMARY_BUCKET, Key="x.txt", Body=b"same")
        s3_bak.put_object(Bucket=BACKUP_BUCKET, Key="x.txt", Body=b"same")

        from cli.commands.drift import drift
        runner = CliRunner()
        result = runner.invoke(drift, [])
        assert result.exit_code == 0
        assert "HEALTHY" in result.output
        assert "100.0%" in result.output

    @mock_aws
    def test_drift_reports_missing_object(self):
        s3_pri = boto3.client("s3", region_name=PRIMARY_REGION)
        s3_bak = boto3.client("s3", region_name=BACKUP_REGION)
        dynamo = boto3.client("dynamodb", region_name=DYNAMO_REGION)
        _bootstrap(s3_pri, s3_bak, dynamo)

        s3_pri.put_object(Bucket=PRIMARY_BUCKET, Key="missing.csv", Body=b"data")

        from cli.commands.drift import drift
        runner = CliRunner()
        result = runner.invoke(drift, [])
        assert result.exit_code == 0
        assert "DRIFT" in result.output
        assert "missing.csv" in result.output

    @mock_aws
    def test_drift_output_format_includes_sync_percentage(self):
        s3_pri = boto3.client("s3", region_name=PRIMARY_REGION)
        s3_bak = boto3.client("s3", region_name=BACKUP_REGION)
        dynamo = boto3.client("dynamodb", region_name=DYNAMO_REGION)
        _bootstrap(s3_pri, s3_bak, dynamo)

        from cli.commands.drift import drift
        runner = CliRunner()
        result = runner.invoke(drift, [])
        assert "%" in result.output
