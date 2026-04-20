"""
Tests for infra/provision.py and infra/teardown.py.
All AWS calls are intercepted by moto.
"""
import os
import sys
import json
import pytest
import boto3
from moto import mock_aws

from tests.conftest import (
    PRIMARY_REGION, BACKUP_REGION, DYNAMO_REGION, IAM_ROLE_NAME,
)

# ---------------------------------------------------------------------------
# Point infra.config at test resource names before importing provision/teardown
# ---------------------------------------------------------------------------
import infra.config as _config_mod

_config_mod.config.project_id = "infratest"
_config_mod.config.primary_region = PRIMARY_REGION
_config_mod.config.backup_region = BACKUP_REGION
_config_mod.config.dynamo_region = DYNAMO_REGION
_config_mod.config.lambda_region = PRIMARY_REGION
_config_mod.config.iam_role_name = IAM_ROLE_NAME
_config_mod.config.dynamo_table = "rescue-replication-log-infratest"
_config_mod.config.replicator_lambda_name = "rescue-replicator-infratest"
_config_mod.config.healthchecker_lambda_name = "rescue-healthchecker-infratest"
_config_mod.config.cloudwatch_rule_name = "rescue-healthcheck-infratest"
_config_mod.config._primary_is_original = True


@pytest.fixture()
def aws_env():
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = PRIMARY_REGION


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dummy_zip() -> bytes:
    """Minimal valid zip with a handler.py for Lambda create."""
    import zipfile, io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("handler.py", "def lambda_handler(e, c): return {}")
    return buf.getvalue()


def _create_iam_role_and_table(iam, dynamo):
    """Pre-create IAM role and DynamoDB table so provision sub-functions can be called independently."""
    trust = json.dumps({
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}],
    })
    resp = iam.create_role(RoleName=IAM_ROLE_NAME, AssumeRolePolicyDocument=trust)
    role_arn = resp["Role"]["Arn"]

    dynamo.create_table(
        TableName=_config_mod.config.dynamo_table,
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
    table_arn = dynamo.describe_table(TableName=_config_mod.config.dynamo_table)["Table"]["TableArn"]
    return role_arn, table_arn


# ---------------------------------------------------------------------------

class TestProvisionBuckets:
    def test_creates_primary_and_backup_buckets(self, aws_env):
        with mock_aws():
            s3_pri = boto3.client("s3", region_name=PRIMARY_REGION)
            s3_bak = boto3.client("s3", region_name=BACKUP_REGION)

            from infra.provision import create_bucket
            create_bucket(s3_pri, _config_mod.config.primary_bucket, PRIMARY_REGION)
            create_bucket(s3_bak, _config_mod.config.backup_bucket, BACKUP_REGION)

            buckets = [b["Name"] for b in s3_pri.list_buckets()["Buckets"]]
            assert _config_mod.config.primary_bucket in buckets

            # Backup bucket accessible from backup region client
            resp = s3_bak.head_bucket(Bucket=_config_mod.config.backup_bucket)
            assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200

    def test_create_bucket_is_idempotent(self, aws_env):
        with mock_aws():
            s3 = boto3.client("s3", region_name=PRIMARY_REGION)
            from infra.provision import create_bucket

            # Should not raise on second call
            create_bucket(s3, _config_mod.config.primary_bucket, PRIMARY_REGION)
            create_bucket(s3, _config_mod.config.primary_bucket, PRIMARY_REGION)

            buckets = [b["Name"] for b in s3.list_buckets()["Buckets"]]
            assert buckets.count(_config_mod.config.primary_bucket) == 1

    def test_bucket_versioning_enabled(self, aws_env):
        with mock_aws():
            s3 = boto3.client("s3", region_name=PRIMARY_REGION)
            from infra.provision import create_bucket
            create_bucket(s3, _config_mod.config.primary_bucket, PRIMARY_REGION)

            resp = s3.get_bucket_versioning(Bucket=_config_mod.config.primary_bucket)
            assert resp.get("Status") == "Enabled"

    def test_bucket_public_access_blocked(self, aws_env):
        with mock_aws():
            s3 = boto3.client("s3", region_name=PRIMARY_REGION)
            from infra.provision import create_bucket
            create_bucket(s3, _config_mod.config.primary_bucket, PRIMARY_REGION)

            resp = s3.get_public_access_block(Bucket=_config_mod.config.primary_bucket)
            config = resp["PublicAccessBlockConfiguration"]
            assert config["BlockPublicAcls"] is True
            assert config["IgnorePublicAcls"] is True
            assert config["BlockPublicPolicy"] is True
            assert config["RestrictPublicBuckets"] is True


class TestProvisionDynamoDB:
    def test_creates_dynamo_table(self, aws_env):
        with mock_aws():
            from infra.provision import provision_dynamodb
            table_arn = provision_dynamodb()

            dynamo = boto3.client("dynamodb", region_name=DYNAMO_REGION)
            desc = dynamo.describe_table(TableName=_config_mod.config.dynamo_table)
            assert desc["Table"]["TableStatus"] == "ACTIVE"

    def test_dynamo_provision_idempotent(self, aws_env):
        with mock_aws():
            from infra.provision import provision_dynamodb
            # Running twice should not raise
            provision_dynamodb()
            provision_dynamodb()

            dynamo = boto3.client("dynamodb", region_name=DYNAMO_REGION)
            tables = dynamo.list_tables()["TableNames"]
            assert tables.count(_config_mod.config.dynamo_table) == 1

    def test_dynamo_key_schema(self, aws_env):
        with mock_aws():
            from infra.provision import provision_dynamodb
            provision_dynamodb()

            dynamo = boto3.client("dynamodb", region_name=DYNAMO_REGION)
            desc = dynamo.describe_table(TableName=_config_mod.config.dynamo_table)
            keys = {k["AttributeName"]: k["KeyType"] for k in desc["Table"]["KeySchema"]}
            assert keys["object_key"] == "HASH"
            assert keys["timestamp"] == "RANGE"


class TestProvisionIAMRole:
    def test_creates_iam_role(self, aws_env):
        with mock_aws():
            iam = boto3.client("iam")
            from infra.provision import provision_iam_role

            primary_arn = f"arn:aws:s3:::{_config_mod.config.primary_bucket}"
            backup_arn = f"arn:aws:s3:::{_config_mod.config.backup_bucket}"
            table_arn = f"arn:aws:dynamodb:{DYNAMO_REGION}:123456789012:table/{_config_mod.config.dynamo_table}"

            role_arn = provision_iam_role(primary_arn, backup_arn, table_arn)
            assert IAM_ROLE_NAME in role_arn

            resp = iam.get_role(RoleName=IAM_ROLE_NAME)
            assert resp["Role"]["RoleName"] == IAM_ROLE_NAME

    def test_iam_role_provision_idempotent(self, aws_env):
        with mock_aws():
            from infra.provision import provision_iam_role
            primary_arn = f"arn:aws:s3:::{_config_mod.config.primary_bucket}"
            backup_arn = f"arn:aws:s3:::{_config_mod.config.backup_bucket}"
            table_arn = f"arn:aws:dynamodb:{DYNAMO_REGION}:123456789012:table/{_config_mod.config.dynamo_table}"

            provision_iam_role(primary_arn, backup_arn, table_arn)
            provision_iam_role(primary_arn, backup_arn, table_arn)  # second call must not raise

    def test_iam_inline_policy_attached(self, aws_env):
        with mock_aws():
            from infra.provision import provision_iam_role
            primary_arn = f"arn:aws:s3:::{_config_mod.config.primary_bucket}"
            backup_arn = f"arn:aws:s3:::{_config_mod.config.backup_bucket}"
            table_arn = f"arn:aws:dynamodb:{DYNAMO_REGION}:123456789012:table/{_config_mod.config.dynamo_table}"

            provision_iam_role(primary_arn, backup_arn, table_arn)

            iam = boto3.client("iam")
            policies = iam.list_role_policies(RoleName=IAM_ROLE_NAME)["PolicyNames"]
            assert "rescue-lambda-policy" in policies

    def test_iam_policy_no_wildcard_resources(self, aws_env):
        with mock_aws():
            from infra.provision import provision_iam_role
            primary_arn = f"arn:aws:s3:::{_config_mod.config.primary_bucket}"
            backup_arn = f"arn:aws:s3:::{_config_mod.config.backup_bucket}"
            table_arn = f"arn:aws:dynamodb:{DYNAMO_REGION}:123456789012:table/{_config_mod.config.dynamo_table}"

            provision_iam_role(primary_arn, backup_arn, table_arn)

            iam = boto3.client("iam")
            doc = iam.get_role_policy(RoleName=IAM_ROLE_NAME, PolicyName="rescue-lambda-policy")
            import urllib.parse
            raw = doc["PolicyDocument"]
            # moto may return a dict or a URL-encoded string depending on version
            if isinstance(raw, dict):
                policy = raw
            else:
                policy = json.loads(urllib.parse.unquote(raw))

            for stmt in policy["Statement"]:
                resources = stmt.get("Resource", [])
                if isinstance(resources, str):
                    resources = [resources]
                for r in resources:
                    # No plain wildcard -- only allow logs:* ARN pattern
                    if r == "*":
                        pytest.fail(f"Wildcard '*' resource found in IAM policy: {stmt}")


class TestTeardown:
    def test_teardown_removes_buckets(self, aws_env):
        with mock_aws():
            # Setup
            s3_pri = boto3.client("s3", region_name=PRIMARY_REGION)
            s3_bak = boto3.client("s3", region_name=BACKUP_REGION)
            s3_pri.create_bucket(Bucket=_config_mod.config.primary_bucket)
            s3_bak.create_bucket(
                Bucket=_config_mod.config.backup_bucket,
                CreateBucketConfiguration={"LocationConstraint": BACKUP_REGION},
            )
            s3_pri.put_object(Bucket=_config_mod.config.primary_bucket, Key="x.txt", Body=b"data")

            from infra import teardown as td
            td.delete_buckets()

            buckets = [b["Name"] for b in s3_pri.list_buckets()["Buckets"]]
            assert _config_mod.config.primary_bucket not in buckets
            assert _config_mod.config.backup_bucket not in buckets

    def test_teardown_removes_dynamo_table(self, aws_env):
        with mock_aws():
            dynamo = boto3.client("dynamodb", region_name=DYNAMO_REGION)
            dynamo.create_table(
                TableName=_config_mod.config.dynamo_table,
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

            from infra import teardown as td
            td.delete_dynamodb()

            tables = dynamo.list_tables()["TableNames"]
            assert _config_mod.config.dynamo_table not in tables

    def test_teardown_graceful_when_resources_missing(self, aws_env):
        with mock_aws():
            # None of the resources exist -- teardown must not raise
            from infra import teardown as td
            td.delete_buckets()
            td.delete_dynamodb()
            td.delete_iam_role()
            # If we get here without exception, test passes

    def test_teardown_removes_iam_role(self, aws_env):
        with mock_aws():
            iam = boto3.client("iam")
            trust = json.dumps({
                "Version": "2012-10-17",
                "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}],
            })
            iam.create_role(RoleName=IAM_ROLE_NAME, AssumeRolePolicyDocument=trust)
            iam.put_role_policy(
                RoleName=IAM_ROLE_NAME,
                PolicyName="rescue-lambda-policy",
                PolicyDocument=json.dumps({
                    "Version": "2012-10-17",
                    "Statement": [{"Effect": "Allow", "Action": "s3:GetObject", "Resource": "*"}],
                }),
            )

            from infra import teardown as td
            td.delete_iam_role()

            from botocore.exceptions import ClientError
            with pytest.raises(ClientError) as exc_info:
                iam.get_role(RoleName=IAM_ROLE_NAME)
            assert exc_info.value.response["Error"]["Code"] == "NoSuchEntity"
