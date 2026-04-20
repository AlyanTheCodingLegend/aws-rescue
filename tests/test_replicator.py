"""
Tests for lambdas/replicator/handler.py
All AWS calls are intercepted by moto -- no real resources are used.

The replicator module creates boto3 clients at import time, so we patch
the module-level client objects after the mock is active.
"""
import importlib
import sys
import os
import pytest
import boto3
from moto import mock_aws
from unittest.mock import patch, MagicMock

from tests.conftest import (
    PRIMARY_BUCKET, BACKUP_BUCKET, PRIMARY_REGION, BACKUP_REGION,
    DYNAMO_TABLE, DYNAMO_REGION, make_s3_event, full_setup,
)


def _load_handler(s3_pri_client, s3_bak_client, dynamo_table_resource):
    """
    Import (or reload) the replicator handler and patch its module-level
    boto3 clients to the moto-backed ones provided.
    """
    if "lambdas.replicator.handler" in sys.modules:
        del sys.modules["lambdas.replicator.handler"]

    import lambdas.replicator.handler as mod
    mod.s3_source = s3_pri_client
    mod.s3_dest = s3_bak_client
    mod.table = dynamo_table_resource
    mod.DEST_BUCKET = BACKUP_BUCKET
    mod.SOURCE_REGION = PRIMARY_REGION
    mod.DEST_REGION = BACKUP_REGION
    return mod


# ---------------------------------------------------------------------------

class TestReplicatorCreated:
    def test_object_created_appears_in_backup(self, full_setup):
        s3_pri, s3_bak, table = full_setup
        s3_pri.put_object(Bucket=PRIMARY_BUCKET, Key="donors/file.csv", Body=b"id,name\n1,Alice")

        mod = _load_handler(s3_pri, s3_bak, table)
        event = make_s3_event(PRIMARY_BUCKET, "donors/file.csv", size=15)
        result = mod.lambda_handler(event, None)

        assert result["processed"] == 1
        assert result["failed"] == 0

        resp = s3_bak.get_object(Bucket=BACKUP_BUCKET, Key="donors/file.csv")
        assert resp["Body"].read() == b"id,name\n1,Alice"

    def test_object_created_logs_success_to_dynamo(self, full_setup):
        s3_pri, s3_bak, table = full_setup
        s3_pri.put_object(Bucket=PRIMARY_BUCKET, Key="reports/r1.txt", Body=b"report content")

        mod = _load_handler(s3_pri, s3_bak, table)
        mod.lambda_handler(make_s3_event(PRIMARY_BUCKET, "reports/r1.txt"), None)

        resp = table.scan(
            FilterExpression="object_key = :k",
            ExpressionAttributeValues={":k": "reports/r1.txt"},
        )
        items = resp["Items"]
        assert len(items) == 1
        assert items[0]["status"] == "SUCCESS"
        assert items[0]["source_region"] == PRIMARY_REGION
        assert items[0]["dest_region"] == BACKUP_REGION

    def test_object_vanished_before_copy_is_handled_gracefully(self, full_setup):
        s3_pri, s3_bak, table = full_setup
        # Do NOT put the object -- simulate it vanishing between event and execution

        mod = _load_handler(s3_pri, s3_bak, table)
        # moto returns NoSuchKey when the object doesn't exist on copy
        # The handler should catch it and NOT raise
        event = make_s3_event(PRIMARY_BUCKET, "ghost/missing.txt", size=0)
        # Should not raise
        result = mod.lambda_handler(event, None)
        # processed increments because the handler returned (no re-raise on NoSuchKey)
        assert result["failed"] == 0

    def test_multiple_records_processed(self, full_setup):
        s3_pri, s3_bak, table = full_setup
        keys = ["a.txt", "b.txt", "c.txt"]
        for k in keys:
            s3_pri.put_object(Bucket=PRIMARY_BUCKET, Key=k, Body=b"data")

        mod = _load_handler(s3_pri, s3_bak, table)
        event = {
            "Records": [
                {
                    "eventName": "ObjectCreated:Put",
                    "s3": {"bucket": {"name": PRIMARY_BUCKET}, "object": {"key": k, "size": 4}},
                }
                for k in keys
            ]
        }
        result = mod.lambda_handler(event, None)
        assert result["processed"] == 3
        assert result["failed"] == 0

        for k in keys:
            head = s3_bak.head_object(Bucket=BACKUP_BUCKET, Key=k)
            assert head["ResponseMetadata"]["HTTPStatusCode"] == 200

    def test_url_encoded_key_decoded_correctly(self, full_setup):
        s3_pri, s3_bak, table = full_setup
        actual_key = "finance/report 2024.csv"
        s3_pri.put_object(Bucket=PRIMARY_BUCKET, Key=actual_key, Body=b"amount,date")

        mod = _load_handler(s3_pri, s3_bak, table)
        encoded_key = "finance/report+2024.csv"
        event = make_s3_event(PRIMARY_BUCKET, encoded_key, size=10)
        result = mod.lambda_handler(event, None)

        assert result["processed"] == 1
        resp = s3_bak.head_object(Bucket=BACKUP_BUCKET, Key=actual_key)
        assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200


class TestReplicatorRemoved:
    def test_object_removed_deletes_from_backup(self, full_setup):
        s3_pri, s3_bak, table = full_setup
        s3_bak.put_object(Bucket=BACKUP_BUCKET, Key="donors/old.csv", Body=b"stale data")

        mod = _load_handler(s3_pri, s3_bak, table)
        event = make_s3_event(PRIMARY_BUCKET, "donors/old.csv", event_name="ObjectRemoved:Delete")
        result = mod.lambda_handler(event, None)

        assert result["processed"] == 1
        import botocore.exceptions
        with pytest.raises(botocore.exceptions.ClientError) as exc_info:
            s3_bak.head_object(Bucket=BACKUP_BUCKET, Key="donors/old.csv")
        assert exc_info.value.response["Error"]["Code"] == "404"

    def test_object_removed_logs_deleted_to_dynamo(self, full_setup):
        s3_pri, s3_bak, table = full_setup
        s3_bak.put_object(Bucket=BACKUP_BUCKET, Key="finance/old.json", Body=b"{}")

        mod = _load_handler(s3_pri, s3_bak, table)
        mod.lambda_handler(
            make_s3_event(PRIMARY_BUCKET, "finance/old.json", event_name="ObjectRemoved:Delete"),
            None,
        )

        resp = table.scan(
            FilterExpression="object_key = :k",
            ExpressionAttributeValues={":k": "finance/old.json"},
        )
        items = resp["Items"]
        assert any(i["status"] == "DELETED" for i in items)

    def test_remove_nonexistent_object_is_graceful(self, full_setup):
        s3_pri, s3_bak, table = full_setup

        mod = _load_handler(s3_pri, s3_bak, table)
        result = mod.lambda_handler(
            make_s3_event(PRIMARY_BUCKET, "never/existed.txt", event_name="ObjectRemoved:Delete"),
            None,
        )
        assert result["failed"] == 0


class TestReplicatorFailure:
    def test_replication_failure_logs_failed_status(self, full_setup):
        s3_pri, s3_bak, table = full_setup
        s3_pri.put_object(Bucket=PRIMARY_BUCKET, Key="projects/p.json", Body=b"{}")

        mod = _load_handler(s3_pri, s3_bak, table)

        # Force copy to raise by making s3_dest.copy raise
        from botocore.exceptions import ClientError
        original_copy = s3_bak.copy

        def _failing_copy(*args, **kwargs):
            error_resp = {"Error": {"Code": "InternalError", "Message": "Simulated failure"}}
            raise ClientError(error_resp, "CopyObject")

        s3_bak.copy = _failing_copy

        with pytest.raises(ClientError):
            mod.lambda_handler(make_s3_event(PRIMARY_BUCKET, "projects/p.json"), None)

        resp = table.scan(
            FilterExpression="object_key = :k",
            ExpressionAttributeValues={":k": "projects/p.json"},
        )
        items = resp["Items"]
        assert any(i["status"] == "FAILED" for i in items)
        failed = next(i for i in items if i["status"] == "FAILED")
        assert "error_message" in failed

        s3_bak.copy = original_copy

    def test_unknown_event_type_is_ignored(self, full_setup):
        s3_pri, s3_bak, table = full_setup

        mod = _load_handler(s3_pri, s3_bak, table)
        event = make_s3_event(PRIMARY_BUCKET, "x.txt", event_name="ObjectRestore:Post")
        result = mod.lambda_handler(event, None)

        assert result["processed"] == 1
        assert result["failed"] == 0
