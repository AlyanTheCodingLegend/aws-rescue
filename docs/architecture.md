# AWS-RESCUE Architecture

## Overview

AWS-RESCUE is a cross-region data replication system built entirely on AWS managed services. It ensures that NGO operational data stored in a primary S3 bucket (`us-east-1`) is continuously mirrored to a backup S3 bucket (`eu-west-1`). If the primary region becomes unavailable, all data remains accessible in the backup region.

---

## Component Diagram

```
┌───────────���───────────────────────��─────────────────────────┐
│                        AWS Account                          │
│                                                             │
│  ┌──────────────────┐          ┌──────────────────┐         │
│  │  PRIMARY REGION  │          │  BACKUP REGION   │         │
│  │  (us-east-1)     │          │  (eu-west-1)     │         │
│  │                  │          │                  │         │
│  │  ┌────────────┐  │  mirror  │  ┌────────────┐  │         │
│  │  │ S3 Bucket  │──┼──────────┼─▶│ S3 Bucket  │  │         │
│  │  │ (primary)  │  │          │  │ (backup)   │  │         │
│  │  └─────┬──────┘  │          │  └────────────┘  │         │
│  │        │ event   │          │                  │         │
│  │  ┌─────▼──────┐  │          │                  │         │
│  │  │  Lambda:   │  │          │                  │         │
│  │  │ Replicator │──┼──────────┼─▶ (writes)       │         │
│  │  └─────┬──────┘  │          │                  │         │
│  │        │         │          │                  │         │
│  │  ┌─────▼──────┐  │          │                  │         │
│  │  │  Lambda:   │  │          │                  │         │
│  │  │HealthCheck │  │          │                  │         │
│  │  └─────┬──────┘  │          │                  │         │
│  │        │         │          │                  │         │
│  │  ┌─────▼──────┐  │          │                  │         │
│  │  │  DynamoDB  │  │          │                  │         │
│  │  │ (log table)│  │          │                  │         │
│  │  └────────────┘  │          │                  │         │
│  └──────────────────┘          └──────────────────┘         │
└─────────────────────────────────────────────────────���───────┘

        ▲ boto3                          ▲ boto3
        │                               │
  ┌─────┴───────────────┐    ┌──────────┴────────────┐
  │  Streamlit Dashboard│    │  CLI (rescue-cli)      │
  │  (localhost:8501)   │    │  (terminal)            │
  └─────────────────────┘    └───────────────────────┘
```

---

## Data Flow

### 1. Normal Upload Flow

1. User or application uploads a file to `rescue-primary-{id}` in `us-east-1`
2. S3 fires an `ObjectCreated` event to the Replicator Lambda (same region)
3. Replicator uses `s3.copy()` with `CopySource` to perform a **server-side cross-region copy** — the object body never passes through Lambda memory
4. Replicator writes a `SUCCESS` log entry to DynamoDB with key, timestamp, size, ETag
5. The file now exists in both buckets

### 2. Delete Flow

1. Object deleted from primary bucket
2. S3 fires `ObjectRemoved` event to Replicator Lambda
3. Replicator deletes the corresponding object from the backup bucket
4. Logs `DELETED` status to DynamoDB

### 3. Health Check Flow (every 15 min)

1. CloudWatch Events triggers HealthChecker Lambda on schedule
2. Lambda paginates both bucket inventories (key, ETag, size)
3. Computes set differences and ETag mismatches
4. If drift found: logs each drifted key to DynamoDB as `DRIFT_DETECTED`
5. If healthy: logs a single `HEALTH_CHECK_OK` entry

### 4. Failover Flow

1. `rescue-cli failover --confirm` rewrites `infra/config.py` toggling `_primary_is_original`
2. All subsequent boto3 calls in CLI and dashboard now target `eu-west-1` as primary
3. Optional `--reverse-sync` copies all objects from new-primary → new-backup
4. Run again with `--confirm` to restore original configuration

---

## Component Details

| Component | Region | Technology | Purpose |
|---|---|---|---|
| S3 Primary | us-east-1 | S3 (versioned, AES-256) | Source of truth |
| S3 Backup | eu-west-1 | S3 (versioned, AES-256) | Cross-region mirror |
| Lambda Replicator | us-east-1 | Python 3.12 | Event-driven copy |
| Lambda HealthChecker | us-east-1 | Python 3.12 | Scheduled drift detection |
| DynamoDB | us-east-1 | On-demand, TTL 30d | Replication audit log |
| CloudWatch Events | us-east-1 | rate(15 minutes) | Schedules HealthChecker |
| IAM Role | global | Least-privilege inline policy | Lambda permissions |

---

## Security Design

- **No public S3 access**: both buckets have all public access blocked
- **Encryption at rest**: AES-256 server-side encryption on both buckets
- **Versioning**: enabled on both buckets — deleted objects are recoverable
- **IAM least privilege**: Lambda role scoped to exact bucket ARNs and table ARN, no wildcards
- **No hardcoded credentials**: all secrets via environment variables or `.env`
- **TTL on logs**: DynamoDB entries expire after 30 days automatically

---

## Operational Notes

- The Replicator Lambda retries up to 2 times on failure (Lambda default retry for async invocations)
- Server-side copy (`s3.copy()` with `SourceClient`) means Lambda memory usage is constant regardless of object size
- Both buckets use versioning, so overwritten objects retain their history
- The dashboard's `st.cache_data(ttl=30)` reduces AWS API calls during normal browsing
