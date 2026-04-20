#!/usr/bin/env bash
# Deploy (or update) both Lambda functions to AWS.
# Run from project root: bash scripts/deploy_lambdas.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/.env" 2>/dev/null || true

PROJECT_ID="${RESCUE_PROJECT_ID:-rescue42}"
REGION="${AWS_DEFAULT_REGION:-us-east-1}"
REPLICATOR_NAME="rescue-replicator"
HEALTHCHECKER_NAME="rescue-healthchecker"
ROLE_NAME="rescue-lambda-role"

echo "=== AWS-RESCUE Lambda Deployment ==="
echo "Region: $REGION | Project: $PROJECT_ID"

# Resolve IAM role ARN
ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query "Role.Arn" --output text)
echo "IAM Role: $ROLE_ARN"

# ---- Package and deploy replicator -----------------------------------------
echo ""
echo "[1/2] Deploying $REPLICATOR_NAME..."
cd "$ROOT/lambdas/replicator"
zip -q replicator.zip handler.py

if aws lambda get-function --function-name "$REPLICATOR_NAME" --region "$REGION" > /dev/null 2>&1; then
  aws lambda update-function-code \
    --function-name "$REPLICATOR_NAME" \
    --zip-file fileb://replicator.zip \
    --region "$REGION" \
    --output text --query "FunctionArn"
  echo "  Updated $REPLICATOR_NAME"
else
  aws lambda create-function \
    --function-name "$REPLICATOR_NAME" \
    --runtime python3.12 \
    --role "$ROLE_ARN" \
    --handler handler.lambda_handler \
    --zip-file fileb://replicator.zip \
    --timeout 60 \
    --memory-size 256 \
    --environment "Variables={DEST_BUCKET=rescue-backup-${PROJECT_ID},DEST_REGION=eu-west-1,SOURCE_REGION=us-east-1,DYNAMO_TABLE=rescue-replication-log,DYNAMO_REGION=us-east-1}" \
    --region "$REGION" \
    --output text --query "FunctionArn"
  echo "  Created $REPLICATOR_NAME"
fi
rm -f replicator.zip

# ---- Package and deploy healthchecker --------------------------------------
echo ""
echo "[2/2] Deploying $HEALTHCHECKER_NAME..."
cd "$ROOT/lambdas/healthchecker"
zip -q healthchecker.zip handler.py

if aws lambda get-function --function-name "$HEALTHCHECKER_NAME" --region "$REGION" > /dev/null 2>&1; then
  aws lambda update-function-code \
    --function-name "$HEALTHCHECKER_NAME" \
    --zip-file fileb://healthchecker.zip \
    --region "$REGION" \
    --output text --query "FunctionArn"
  echo "  Updated $HEALTHCHECKER_NAME"
else
  aws lambda create-function \
    --function-name "$HEALTHCHECKER_NAME" \
    --runtime python3.12 \
    --role "$ROLE_ARN" \
    --handler handler.lambda_handler \
    --zip-file fileb://healthchecker.zip \
    --timeout 60 \
    --memory-size 256 \
    --environment "Variables={PRIMARY_BUCKET=rescue-primary-${PROJECT_ID},BACKUP_BUCKET=rescue-backup-${PROJECT_ID},PRIMARY_REGION=us-east-1,BACKUP_REGION=eu-west-1,DYNAMO_TABLE=rescue-replication-log,DYNAMO_REGION=us-east-1}" \
    --region "$REGION" \
    --output text --query "FunctionArn"
  echo "  Created $HEALTHCHECKER_NAME"
fi
rm -f healthchecker.zip

echo ""
echo "=== Deployment complete ==="
