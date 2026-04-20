"""
Tests for lambdas/healthchecker/handler.py
All AWS calls are intercepted by moto.
"""
import sys
import pytest
import boto3
from moto import mock_aws

from tests.conftest import (
    PRIMARY_BUCKET, BACKUP_BUCKET, PRIMARY_REGION, BACKUP_REGION,
    DYNAMO_TABLE, DYNAMO_REGION, full_setup,
)


def _load_handler(s3_pri_client, s3_bak_client, dynamo_table_resource):
    if "lambdas.healthchecker.handler" in sys.modules:
        del sys.modules["lambdas.healthchecker.handler"]

    import lambdas.healthchecker.handler as mod
    mod.s3_primary = s3_pri_client
    mod.s3_backup = s3_bak_client
    mod.table = dynamo_table_resource
    mod.PRIMARY_BUCKET = PRIMARY_BUCKET
    mod.BACKUP_BUCKET = BACKUP_BUCKET
    mod.PRIMARY_REGION = PRIMARY_REGION
    mod.BACKUP_REGION = BACKUP_REGION
    return mod


def _put(s3_client, bucket, key, body=b"data"):
    s3_client.put_object(Bucket=bucket, Key=key, Body=body)


# ---------------------------------------------------------------------------

class TestHealthCheckerHealthy:
    def test_identical_buckets_reports_healthy(self, full_setup):
        s3_pri, s3_bak, table = full_setup
        for k in ["a.txt", "b.txt", "c.txt"]:
            _put(s3_pri, PRIMARY_BUCKET, k)
            _put(s3_bak, BACKUP_BUCKET, k)

        mod = _load_handler(s3_pri, s3_bak, table)
        result = mod.lambda_handler({}, None)

        assert result["status"] == "HEALTHY"
        assert result["sync_percentage"] == 100.0
        assert result["missing_in_backup"] == 0
        assert result["missing_in_primary"] == 0
        assert result["mismatched"] == 0
        assert result["in_sync"] == 3

    def test_healthy_logs_health_check_ok_to_dynamo(self, full_setup):
        s3_pri, s3_bak, table = full_setup
        _put(s3_pri, PRIMARY_BUCKET, "x.txt")
        _put(s3_bak, BACKUP_BUCKET, "x.txt")

        mod = _load_handler(s3_pri, s3_bak, table)
        mod.lambda_handler({}, None)

        resp = table.scan(
            FilterExpression="object_key = :k AND #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":k": "__health_check__", ":s": "HEALTH_CHECK_OK"},
        )
        assert len(resp["Items"]) >= 1

    def test_empty_buckets_reports_healthy_100_pct(self, full_setup):
        s3_pri, s3_bak, table = full_setup

        mod = _load_handler(s3_pri, s3_bak, table)
        result = mod.lambda_handler({}, None)

        assert result["status"] == "HEALTHY"
        assert result["sync_percentage"] == 100.0
        assert result["primary_count"] == 0
        assert result["backup_count"] == 0


class TestHealthCheckerDrift:
    def test_missing_in_backup_detected(self, full_setup):
        s3_pri, s3_bak, table = full_setup
        _put(s3_pri, PRIMARY_BUCKET, "missing.txt")
        # Do NOT put in backup

        mod = _load_handler(s3_pri, s3_bak, table)
        result = mod.lambda_handler({}, None)

        assert result["status"] == "DRIFT"
        assert result["missing_in_backup"] == 1
        assert "missing.txt" in result["missing_in_backup_keys"]

    def test_missing_in_backup_logs_drift_detected(self, full_setup):
        s3_pri, s3_bak, table = full_setup
        _put(s3_pri, PRIMARY_BUCKET, "orphan.csv")

        mod = _load_handler(s3_pri, s3_bak, table)
        mod.lambda_handler({}, None)

        resp = table.scan(
            FilterExpression="object_key = :k AND #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":k": "orphan.csv", ":s": "DRIFT_DETECTED"},
        )
        assert len(resp["Items"]) >= 1

    def test_orphan_in_backup_detected(self, full_setup):
        s3_pri, s3_bak, table = full_setup
        _put(s3_bak, BACKUP_BUCKET, "extra.json")
        # Do NOT put in primary

        mod = _load_handler(s3_pri, s3_bak, table)
        result = mod.lambda_handler({}, None)

        assert result["status"] == "DRIFT"
        assert result["missing_in_primary"] == 1
        assert "extra.json" in result["missing_in_primary_keys"]

    def test_etag_mismatch_detected(self, full_setup):
        s3_pri, s3_bak, table = full_setup
        _put(s3_pri, PRIMARY_BUCKET, "report.txt", body=b"version 1")
        _put(s3_bak, BACKUP_BUCKET, "report.txt", body=b"version 2 -- different content")

        mod = _load_handler(s3_pri, s3_bak, table)
        result = mod.lambda_handler({}, None)

        assert result["status"] == "DRIFT"
        assert result["mismatched"] == 1
        assert "report.txt" in result["mismatched_keys"]

    def test_mixed_drift_types_all_counted(self, full_setup):
        s3_pri, s3_bak, table = full_setup

        # In sync
        _put(s3_pri, PRIMARY_BUCKET, "ok.txt")
        _put(s3_bak, BACKUP_BUCKET, "ok.txt")

        # Missing in backup
        _put(s3_pri, PRIMARY_BUCKET, "missing.txt")

        # Orphan in backup
        _put(s3_bak, BACKUP_BUCKET, "orphan.txt")

        # ETag mismatch
        _put(s3_pri, PRIMARY_BUCKET, "mismatch.txt", body=b"v1")
        _put(s3_bak, BACKUP_BUCKET, "mismatch.txt", body=b"v2")

        mod = _load_handler(s3_pri, s3_bak, table)
        result = mod.lambda_handler({}, None)

        assert result["status"] == "DRIFT"
        assert result["in_sync"] == 1
        assert result["missing_in_backup"] == 1
        assert result["missing_in_primary"] == 1
        assert result["mismatched"] == 1

    def test_sync_percentage_calculation(self, full_setup):
        s3_pri, s3_bak, table = full_setup

        for i in range(8):
            _put(s3_pri, PRIMARY_BUCKET, f"synced_{i}.txt")
            _put(s3_bak, BACKUP_BUCKET, f"synced_{i}.txt")

        for i in range(2):
            _put(s3_pri, PRIMARY_BUCKET, f"missing_{i}.txt")

        mod = _load_handler(s3_pri, s3_bak, table)
        result = mod.lambda_handler({}, None)

        assert result["primary_count"] == 10
        assert result["in_sync"] == 8
        assert result["sync_percentage"] == 80.0

    def test_result_contains_required_keys(self, full_setup):
        s3_pri, s3_bak, table = full_setup

        mod = _load_handler(s3_pri, s3_bak, table)
        result = mod.lambda_handler({}, None)

        required = {
            "timestamp", "primary_count", "backup_count", "in_sync",
            "missing_in_backup", "missing_in_primary", "mismatched",
            "sync_percentage", "status",
        }
        assert required.issubset(result.keys())
