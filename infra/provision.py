"""
Provision all AWS resources for AWS-RESCUE.
Idempotent -- safe to run multiple times.
"""
import json
import time
import zipfile
import io
import os
import sys

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from infra.config import config


def _tags_list():
    return [{"Key": k, "Value": v} for k, v in config.tags.items()]


def _pbucket(pid: str | None) -> str:
    return f"rescue-primary-{pid or config.project_id}"


def _bbucket(pid: str | None) -> str:
    return f"rescue-backup-{pid or config.project_id}"


# ---------------------------------------------------------------------------
# S3
# ---------------------------------------------------------------------------

def create_bucket(s3, bucket_name: str, region: str):
    try:
        if region == "us-east-1":
            s3.create_bucket(Bucket=bucket_name)
        else:
            s3.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={"LocationConstraint": region},
            )
        print(f"  [+] Created bucket: {bucket_name} ({region})")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            print(f"  [~] Bucket already exists: {bucket_name}")
        else:
            raise

    s3.put_public_access_block(
        Bucket=bucket_name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_versioning(
        Bucket=bucket_name,
        VersioningConfiguration={"Status": "Enabled"},
    )
    s3.put_bucket_encryption(
        Bucket=bucket_name,
        ServerSideEncryptionConfiguration={
            "Rules": [
                {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
            ]
        },
    )
    s3.put_bucket_tagging(
        Bucket=bucket_name,
        Tagging={"TagSet": _tags_list()},
    )


def provision_buckets(bkw: dict | None = None, pid: str | None = None):
    bkw = bkw or {}
    print("\n[1/5] Provisioning S3 buckets...")
    primary_s3 = boto3.client("s3", region_name=config.primary_region, **bkw)
    backup_s3 = boto3.client("s3", region_name=config.backup_region, **bkw)

    pb = _pbucket(pid)
    bb = _bbucket(pid)

    create_bucket(primary_s3, pb, config.primary_region)
    create_bucket(backup_s3, bb, config.backup_region)

    primary_arn = f"arn:aws:s3:::{pb}"
    backup_arn = f"arn:aws:s3:::{bb}"
    print(f"  Primary ARN : {primary_arn}")
    print(f"  Backup ARN  : {backup_arn}")
    return primary_arn, backup_arn


# ---------------------------------------------------------------------------
# DynamoDB
# ---------------------------------------------------------------------------

def provision_dynamodb(bkw: dict | None = None):
    bkw = bkw or {}
    print("\n[2/5] Provisioning DynamoDB table...")
    dynamo = boto3.client("dynamodb", region_name=config.dynamo_region, **bkw)

    try:
        dynamo.create_table(
            TableName=config.dynamo_table,
            KeySchema=[
                {"AttributeName": "object_key", "KeyType": "HASH"},
                {"AttributeName": "timestamp", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "object_key", "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
            Tags=_tags_list(),
        )
        waiter = dynamo.get_waiter("table_exists")
        waiter.wait(TableName=config.dynamo_table)
        print(f"  [+] Created DynamoDB table: {config.dynamo_table}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceInUseException":
            print(f"  [~] DynamoDB table already exists: {config.dynamo_table}")
        else:
            raise

    try:
        dynamo.update_time_to_live(
            TableName=config.dynamo_table,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "expiry_ttl"},
        )
        print("  [+] TTL enabled on expiry_ttl attribute")
    except ClientError as e:
        print(f"  [~] TTL update: {e.response['Error']['Message']}")

    table_arn = dynamo.describe_table(TableName=config.dynamo_table)["Table"]["TableArn"]
    print(f"  Table ARN: {table_arn}")
    return table_arn


# ---------------------------------------------------------------------------
# IAM Role
# ---------------------------------------------------------------------------

def provision_iam_role(
    primary_bucket_arn: str,
    backup_bucket_arn: str,
    table_arn: str,
    bkw: dict | None = None,
):
    bkw = bkw or {}
    print("\n[3/5] Provisioning IAM role...")
    iam = boto3.client("iam", **bkw)

    trust_policy = json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    })

    try:
        resp = iam.create_role(
            RoleName=config.iam_role_name,
            AssumeRolePolicyDocument=trust_policy,
            Tags=_tags_list(),
        )
        role_arn = resp["Role"]["Arn"]
        print(f"  [+] Created IAM role: {config.iam_role_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            role_arn = iam.get_role(RoleName=config.iam_role_name)["Role"]["Arn"]
            print(f"  [~] IAM role already exists: {config.iam_role_name}")
        else:
            raise

    inline_policy = json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
                "Resource": [
                    primary_bucket_arn,
                    f"{primary_bucket_arn}/*",
                    backup_bucket_arn,
                    f"{backup_bucket_arn}/*",
                ],
            },
            {
                "Effect": "Allow",
                "Action": ["dynamodb:PutItem", "dynamodb:Query", "dynamodb:Scan", "dynamodb:GetItem"],
                "Resource": [table_arn],
            },
            {
                "Effect": "Allow",
                "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": "arn:aws:logs:*:*:*",
            },
        ],
    })

    iam.put_role_policy(
        RoleName=config.iam_role_name,
        PolicyName="rescue-lambda-policy",
        PolicyDocument=inline_policy,
    )
    print(f"  [+] Inline policy attached to {config.iam_role_name}")
    print(f"  Role ARN: {role_arn}")
    return role_arn


# ---------------------------------------------------------------------------
# Lambda packaging helpers
# ---------------------------------------------------------------------------

def _zip_handler(handler_path: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(handler_path, "handler.py")
    return buf.getvalue()


def _get_account_id(bkw: dict | None = None) -> str:
    return boto3.client("sts", **bkw or {}).get_caller_identity()["Account"]


# ---------------------------------------------------------------------------
# Lambda: Replicator
# ---------------------------------------------------------------------------

def provision_replicator(role_arn: str, bkw: dict | None = None, pid: str | None = None):
    bkw = bkw or {}
    print("\n[4/5] Provisioning Lambda functions...")
    lam = boto3.client("lambda", region_name=config.lambda_region, **bkw)

    handler_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "lambdas", "replicator", "handler.py",
    )
    zip_bytes = _zip_handler(handler_path)

    env_vars = {
        "DEST_BUCKET": _bbucket(pid),
        "DEST_REGION": config.backup_region,
        "SOURCE_REGION": config.primary_region,
        "DYNAMO_TABLE": config.dynamo_table,
        "DYNAMO_REGION": config.dynamo_region,
    }

    try:
        resp = lam.create_function(
            FunctionName=config.replicator_lambda_name,
            Runtime=config.lambda_runtime,
            Role=role_arn,
            Handler="handler.lambda_handler",
            Code={"ZipFile": zip_bytes},
            Timeout=config.lambda_timeout,
            MemorySize=config.lambda_memory,
            Environment={"Variables": env_vars},
            Tags=config.tags,
        )
        replicator_arn = resp["FunctionArn"]
        print(f"  [+] Created Lambda: {config.replicator_lambda_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceConflictException":
            lam.update_function_code(
                FunctionName=config.replicator_lambda_name,
                ZipFile=zip_bytes,
            )
            lam.update_function_configuration(
                FunctionName=config.replicator_lambda_name,
                Environment={"Variables": env_vars},
            )
            replicator_arn = lam.get_function(
                FunctionName=config.replicator_lambda_name
            )["Configuration"]["FunctionArn"]
            print(f"  [~] Updated Lambda: {config.replicator_lambda_name}")
        else:
            raise

    waiter = lam.get_waiter("function_active")
    waiter.wait(FunctionName=config.replicator_lambda_name)

    account_id = _get_account_id(bkw)
    try:
        lam.add_permission(
            FunctionName=config.replicator_lambda_name,
            StatementId="s3-invoke-replicator",
            Action="lambda:InvokeFunction",
            Principal="s3.amazonaws.com",
            SourceArn=f"arn:aws:s3:::{_pbucket(pid)}",
            SourceAccount=account_id,
        )
        print("  [+] S3 invoke permission added to replicator")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceConflictException":
            print("  [~] S3 invoke permission already exists")
        else:
            raise

    print(f"  Replicator ARN: {replicator_arn}")
    return replicator_arn


# ---------------------------------------------------------------------------
# Lambda: HealthChecker
# ---------------------------------------------------------------------------

def provision_healthchecker(role_arn: str, bkw: dict | None = None, pid: str | None = None):
    bkw = bkw or {}
    lam = boto3.client("lambda", region_name=config.lambda_region, **bkw)
    events = boto3.client("events", region_name=config.lambda_region, **bkw)

    handler_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "lambdas", "healthchecker", "handler.py",
    )
    zip_bytes = _zip_handler(handler_path)

    env_vars = {
        "PRIMARY_BUCKET": _pbucket(pid),
        "BACKUP_BUCKET": _bbucket(pid),
        "PRIMARY_REGION": config.primary_region,
        "BACKUP_REGION": config.backup_region,
        "DYNAMO_TABLE": config.dynamo_table,
        "DYNAMO_REGION": config.dynamo_region,
    }

    try:
        resp = lam.create_function(
            FunctionName=config.healthchecker_lambda_name,
            Runtime=config.lambda_runtime,
            Role=role_arn,
            Handler="handler.lambda_handler",
            Code={"ZipFile": zip_bytes},
            Timeout=config.lambda_timeout,
            MemorySize=config.lambda_memory,
            Environment={"Variables": env_vars},
            Tags=config.tags,
        )
        hc_arn = resp["FunctionArn"]
        print(f"  [+] Created Lambda: {config.healthchecker_lambda_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceConflictException":
            lam.update_function_code(
                FunctionName=config.healthchecker_lambda_name,
                ZipFile=zip_bytes,
            )
            lam.update_function_configuration(
                FunctionName=config.healthchecker_lambda_name,
                Environment={"Variables": env_vars},
            )
            hc_arn = lam.get_function(
                FunctionName=config.healthchecker_lambda_name
            )["Configuration"]["FunctionArn"]
            print(f"  [~] Updated Lambda: {config.healthchecker_lambda_name}")
        else:
            raise

    waiter = lam.get_waiter("function_active")
    waiter.wait(FunctionName=config.healthchecker_lambda_name)

    try:
        rule_resp = events.put_rule(
            Name=config.cloudwatch_rule_name,
            ScheduleExpression=config.healthcheck_schedule,
            State="ENABLED",
            Tags=_tags_list(),
        )
        rule_arn = rule_resp["RuleArn"]
        print(f"  [+] CloudWatch Events rule: {config.cloudwatch_rule_name}")
    except ClientError:
        rule_arn = events.describe_rule(Name=config.cloudwatch_rule_name)["Arn"]
        print("  [~] CloudWatch Events rule already exists")

    events.put_targets(
        Rule=config.cloudwatch_rule_name,
        Targets=[{"Id": "healthchecker-target", "Arn": hc_arn}],
    )

    try:
        lam.add_permission(
            FunctionName=config.healthchecker_lambda_name,
            StatementId="events-invoke-healthchecker",
            Action="lambda:InvokeFunction",
            Principal="events.amazonaws.com",
            SourceArn=rule_arn,
        )
        print("  [+] CloudWatch Events invoke permission added to healthchecker")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceConflictException":
            print("  [~] CloudWatch Events invoke permission already exists")
        else:
            raise

    print(f"  HealthChecker ARN: {hc_arn}")
    return hc_arn


# ---------------------------------------------------------------------------
# S3 event notification (wires primary bucket -> replicator lambda)
# ---------------------------------------------------------------------------

def provision_s3_notification(
    replicator_arn: str,
    bkw: dict | None = None,
    pid: str | None = None,
):
    bkw = bkw or {}
    print("\n[5/5] Wiring S3 event notifications...")
    s3 = boto3.client("s3", region_name=config.primary_region, **bkw)
    pb = _pbucket(pid)

    s3.put_bucket_notification_configuration(
        Bucket=pb,
        NotificationConfiguration={
            "LambdaFunctionConfigurations": [
                {
                    "LambdaFunctionArn": replicator_arn,
                    "Events": ["s3:ObjectCreated:*", "s3:ObjectRemoved:*"],
                }
            ]
        },
    )
    print(f"  [+] S3 event notification set: {pb} -> {config.replicator_lambda_name}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def provision(bkw: dict | None = None, pid: str | None = None):
    bkw = bkw or {}
    print("=" * 60)
    print("AWS-RESCUE: Infrastructure Provisioning")
    print("=" * 60)
    print(f"Project ID    : {pid or config.project_id}")
    print(f"Primary Region: {config.primary_region}  Bucket: {_pbucket(pid)}")
    print(f"Backup Region : {config.backup_region}  Bucket: {_bbucket(pid)}")
    print(f"DynamoDB      : {config.dynamo_table} ({config.dynamo_region})")

    primary_arn, backup_arn = provision_buckets(bkw=bkw, pid=pid)
    table_arn = provision_dynamodb(bkw=bkw)

    print("\n[3/5] Provisioning IAM role (waiting 10s after creation for propagation)...")
    role_arn = provision_iam_role(primary_arn, backup_arn, table_arn, bkw=bkw)
    time.sleep(10)

    replicator_arn = provision_replicator(role_arn, bkw=bkw, pid=pid)
    hc_arn = provision_healthchecker(role_arn, bkw=bkw, pid=pid)
    provision_s3_notification(replicator_arn, bkw=bkw, pid=pid)

    print("\n" + "=" * 60)
    print("Provisioning complete. Resource summary:")
    print(f"  S3 Primary    : {primary_arn}")
    print(f"  S3 Backup     : {backup_arn}")
    print(f"  DynamoDB      : {table_arn}")
    print(f"  IAM Role      : {role_arn}")
    print(f"  Replicator λ  : {replicator_arn}")
    print(f"  HealthChecker λ: {hc_arn}")
    print("=" * 60)

    return {
        "primary_bucket": primary_arn,
        "backup_bucket": backup_arn,
        "dynamo_table": table_arn,
        "iam_role": role_arn,
        "replicator_lambda": replicator_arn,
        "healthchecker_lambda": hc_arn,
    }


if __name__ == "__main__":
    provision()
