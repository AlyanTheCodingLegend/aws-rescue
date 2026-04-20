"""
Shared pytest fixtures for all AWS-RESCUE tests.
All AWS calls are intercepted by moto -- no real AWS resources are created.
"""
import os
import json
import pytest
import boto3
from moto import mock_aws

# ---------------------------------------------------------------------------
# Env vars required by the Lambda modules at import time
# ---------------------------------------------------------------------------
PRIMARY_BUCKET = "rescue-primary-test"
BACKUP_BUCKET = "rescue-backup-test"
PRIMARY_REGION = "us-east-1"
BACKUP_REGION = "eu-west-1"
DYNAMO_TABLE = "rescue-replication-log-test"
DYNAMO_REGION = "us-east-1"
IAM_ROLE_NAME = "rescue-lambda-role-test"

os.environ.setdefault("DEST_BUCKET", BACKUP_BUCKET)
os.environ.setdefault("DEST_REGION", BACKUP_REGION)
os.environ.setdefault("SOURCE_REGION", PRIMARY_REGION)
os.environ.setdefault("DYNAMO_TABLE", DYNAMO_TABLE)
os.environ.setdefault("DYNAMO_REGION", DYNAMO_REGION)
os.environ.setdefault("PRIMARY_BUCKET", PRIMARY_BUCKET)
os.environ.setdefault("BACKUP_BUCKET", BACKUP_BUCKET)
os.environ.setdefault("PRIMARY_REGION", PRIMARY_REGION)
os.environ.setdefault("BACKUP_REGION", BACKUP_REGION)
os.environ.setdefault("RESCUE_PROJECT_ID", "test")


@pytest.fixture(scope="function")
def aws_credentials():
    """Dummy credentials so boto3 doesn't try real AWS."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = PRIMARY_REGION


@pytest.fixture(scope="function")
def s3_setup(aws_credentials):
    """Create both S3 buckets inside a moto mock."""
    with mock_aws():
        s3_pri = boto3.client("s3", region_name=PRIMARY_REGION)
        s3_bak = boto3.client("s3", region_name=BACKUP_REGION)

        s3_pri.create_bucket(Bucket=PRIMARY_BUCKET)
        s3_bak.create_bucket(
            Bucket=BACKUP_BUCKET,
            CreateBucketConfiguration={"LocationConstraint": BACKUP_REGION},
        )
        yield s3_pri, s3_bak


@pytest.fixture(scope="function")
def dynamo_setup(aws_credentials):
    """Create the DynamoDB replication log table inside a moto mock."""
    with mock_aws():
        dynamo = boto3.client("dynamodb", region_name=DYNAMO_REGION)
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
        yield boto3.resource("dynamodb", region_name=DYNAMO_REGION).Table(DYNAMO_TABLE)


@pytest.fixture(scope="function")
def full_setup(aws_credentials):
    """Both S3 buckets + DynamoDB table -- used by most tests."""
    with mock_aws():
        # S3
        s3_pri = boto3.client("s3", region_name=PRIMARY_REGION)
        s3_bak = boto3.client("s3", region_name=BACKUP_REGION)
        s3_pri.create_bucket(Bucket=PRIMARY_BUCKET)
        s3_bak.create_bucket(
            Bucket=BACKUP_BUCKET,
            CreateBucketConfiguration={"LocationConstraint": BACKUP_REGION},
        )

        # DynamoDB
        dynamo = boto3.client("dynamodb", region_name=DYNAMO_REGION)
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
        table = boto3.resource("dynamodb", region_name=DYNAMO_REGION).Table(DYNAMO_TABLE)
        yield s3_pri, s3_bak, table


def make_s3_event(bucket: str, key: str, event_name: str = "ObjectCreated:Put", size: int = 100) -> dict:
    """Build a minimal S3 event record as Lambda receives it."""
    return {
        "Records": [
            {
                "eventName": event_name,
                "s3": {
                    "bucket": {"name": bucket},
                    "object": {"key": key, "size": size},
                },
            }
        ]
    }
