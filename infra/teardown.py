"""
Destroy all AWS-RESCUE resources in reverse provisioning order.
Handles 'not found' gracefully — safe to run on partially-provisioned stacks.
"""
import os
import sys

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from infra.config import config


def _not_found(e: ClientError) -> bool:
    code = e.response["Error"]["Code"]
    return code in (
        "NoSuchBucket", "NoSuchEntity", "ResourceNotFoundException",
        "ResourceNotFound", "NoSuchKey", "404", "NoSuchConfiguration",
    )


# ---------------------------------------------------------------------------
# Step 1: Remove S3 event notifications
# ---------------------------------------------------------------------------

def remove_s3_notifications():
    print("\n[1/7] Removing S3 event notifications...")
    s3 = boto3.client("s3", region_name=config.primary_region)
    try:
        s3.put_bucket_notification_configuration(
            Bucket=config.primary_bucket,
            NotificationConfiguration={},
        )
        print(f"  [+] Cleared notifications on {config.primary_bucket}")
    except ClientError as e:
        if _not_found(e):
            print(f"  [~] Bucket not found: {config.primary_bucket}")
        else:
            print(f"  [!] {e.response['Error']['Message']}")


# ---------------------------------------------------------------------------
# Step 2: Delete Lambda functions
# ---------------------------------------------------------------------------

def delete_lambdas():
    print("\n[2/7] Deleting Lambda functions...")
    lam = boto3.client("lambda", region_name=config.lambda_region)
    for name in [config.replicator_lambda_name, config.healthchecker_lambda_name]:
        try:
            lam.delete_function(FunctionName=name)
            print(f"  [+] Deleted Lambda: {name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                print(f"  [~] Lambda not found: {name}")
            else:
                print(f"  [!] {e.response['Error']['Message']}")


# ---------------------------------------------------------------------------
# Step 3: Delete CloudWatch Events rule
# ---------------------------------------------------------------------------

def delete_cloudwatch_rule():
    print("\n[3/7] Deleting CloudWatch Events rule...")
    events = boto3.client("events", region_name=config.lambda_region)
    try:
        # Must remove targets before deleting rule
        events.remove_targets(
            Rule=config.cloudwatch_rule_name,
            Ids=["healthchecker-target"],
        )
        events.delete_rule(Name=config.cloudwatch_rule_name)
        print(f"  [+] Deleted CloudWatch rule: {config.cloudwatch_rule_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("ResourceNotFoundException", "ResourceNotFound"):
            print(f"  [~] CloudWatch rule not found: {config.cloudwatch_rule_name}")
        else:
            print(f"  [!] {e.response['Error']['Message']}")


# ---------------------------------------------------------------------------
# Step 4: Delete DynamoDB table
# ---------------------------------------------------------------------------

def delete_dynamodb():
    print("\n[4/7] Deleting DynamoDB table...")
    dynamo = boto3.client("dynamodb", region_name=config.dynamo_region)
    try:
        dynamo.delete_table(TableName=config.dynamo_table)
        waiter = dynamo.get_waiter("table_not_exists")
        waiter.wait(TableName=config.dynamo_table)
        print(f"  [+] Deleted DynamoDB table: {config.dynamo_table}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"  [~] Table not found: {config.dynamo_table}")
        else:
            print(f"  [!] {e.response['Error']['Message']}")


# ---------------------------------------------------------------------------
# Step 5 & 6: Empty and delete S3 buckets
# ---------------------------------------------------------------------------

def _empty_bucket(s3, bucket_name: str):
    """Delete all object versions and delete markers."""
    paginator = s3.get_paginator("list_object_versions")
    try:
        for page in paginator.paginate(Bucket=bucket_name):
            objects_to_delete = []
            for v in page.get("Versions", []):
                objects_to_delete.append({"Key": v["Key"], "VersionId": v["VersionId"]})
            for dm in page.get("DeleteMarkers", []):
                objects_to_delete.append({"Key": dm["Key"], "VersionId": dm["VersionId"]})
            if objects_to_delete:
                s3.delete_objects(
                    Bucket=bucket_name,
                    Delete={"Objects": objects_to_delete, "Quiet": True},
                )
        print(f"  [+] Emptied bucket: {bucket_name}")
    except ClientError as e:
        if _not_found(e):
            print(f"  [~] Bucket not found (skip empty): {bucket_name}")
        else:
            raise


def delete_buckets():
    print("\n[5/7] Emptying and deleting S3 buckets...")
    primary_s3 = boto3.client("s3", region_name=config.primary_region)
    backup_s3 = boto3.client("s3", region_name=config.backup_region)

    for s3_client, bucket in [
        (primary_s3, config.primary_bucket),
        (backup_s3, config.backup_bucket),
    ]:
        _empty_bucket(s3_client, bucket)
        try:
            s3_client.delete_bucket(Bucket=bucket)
            print(f"  [+] Deleted bucket: {bucket}")
        except ClientError as e:
            if _not_found(e):
                print(f"  [~] Bucket not found: {bucket}")
            else:
                print(f"  [!] {e.response['Error']['Message']}")


# ---------------------------------------------------------------------------
# Step 7: Delete IAM role + inline policies
# ---------------------------------------------------------------------------

def delete_iam_role():
    print("\n[6/7] Deleting IAM role...")
    iam = boto3.client("iam")
    try:
        # Detach all inline policies first
        policies = iam.list_role_policies(RoleName=config.iam_role_name).get("PolicyNames", [])
        for policy_name in policies:
            iam.delete_role_policy(RoleName=config.iam_role_name, PolicyName=policy_name)
            print(f"  [+] Deleted inline policy: {policy_name}")

        # Detach any managed policies
        attached = iam.list_attached_role_policies(RoleName=config.iam_role_name).get("AttachedPolicies", [])
        for p in attached:
            iam.detach_role_policy(RoleName=config.iam_role_name, PolicyArn=p["PolicyArn"])
            print(f"  [+] Detached managed policy: {p['PolicyArn']}")

        iam.delete_role(RoleName=config.iam_role_name)
        print(f"  [+] Deleted IAM role: {config.iam_role_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            print(f"  [~] IAM role not found: {config.iam_role_name}")
        else:
            print(f"  [!] {e.response['Error']['Message']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def teardown():
    print("=" * 60)
    print("AWS-RESCUE: Teardown")
    print("=" * 60)
    print(f"Project ID    : {config.project_id}")
    print(f"Primary Bucket: {config.primary_bucket}")
    print(f"Backup Bucket : {config.backup_bucket}")

    response = input("\nThis will DELETE all AWS-RESCUE resources. Type 'yes' to confirm: ")
    if response.strip().lower() != "yes":
        print("Aborted.")
        return

    remove_s3_notifications()
    delete_lambdas()
    delete_cloudwatch_rule()
    delete_dynamodb()
    delete_buckets()
    delete_iam_role()

    print("\n" + "=" * 60)
    print("Teardown complete. All resources removed.")
    print("=" * 60)


if __name__ == "__main__":
    teardown()
