# AWS-RESCUE: Implementation Specification

> **Purpose**: This document is a complete implementation spec for Claude Code. Follow it top-to-bottom to build the entire project.

## 1. Project Overview

**AWS-RESCUE** (Resilient Emergency Storage & Cross-region Upload Engine) is a cross-region data mirroring tool that ensures NGO data survival during regional AWS outages. It replicates objects across S3 buckets in different AWS regions, monitors replication health, and provides a dashboard + CLI for operations.

**Course**: CS432 — Parallel & Distributed Computing (PDC)  
**Team Size**: 3 members  
**Key Constraint**: Must stay within AWS Free Tier (S3 5GB, Lambda 1M invocations/400K GB-sec, SNS 1M publishes — all always-free). Use fake/sample NGO data files.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────┐
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
│  │  │    SNS     │  │          │                  │         │
│  │  │ (alerts)   │  │          │                  │         │
│  │  └────────────┘  │          │                  │         │
│  └──────────────────┘          └──────────────────┘         │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  DynamoDB (us-east-1) — replication log / metadata   │   │
│  │  (25GB free tier)                                    │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘

         ▲ boto3 queries               ▲ boto3 commands
         │                             │
   ┌─────┴──────────────┐    ┌────────┴──────────────┐
   │  Streamlit Dashboard│    │  CLI (rescue-cli)     │
   │  (runs locally)     │    │  (runs locally)       │
   └─────────────────────┘    └───────────────────────┘
```

### Core Components

| Component | What It Does |
|---|---|
| **S3 Primary Bucket** (`rescue-primary-{id}`) | Stores NGO files in `us-east-1` |
| **S3 Backup Bucket** (`rescue-backup-{id}`) | Cross-region mirror in `eu-west-1` |
| **Lambda: Replicator** | Triggered by S3 events on primary bucket. Copies new/updated objects to backup bucket. Logs to DynamoDB. |
| **Lambda: HealthChecker** | Runs on CloudWatch schedule (every 15 min). Compares primary vs backup inventories. Publishes drift alerts to SNS. |
| **DynamoDB Table** | `rescue-replication-log` — stores per-object replication records (key, status, timestamp, size, checksum) |
| **SNS Topic** | `rescue-alerts` — sends email notifications on replication failures or drift |
| **CLI (`rescue-cli`)** | Python Click CLI for manual operations: upload, sync, status, drift-check, failover |
| **Streamlit Dashboard** | Real-time UI showing replication status, sync health, object inventory, manual controls |

---

## 3. Directory Structure

```
aws-rescue/
├── README.md                    # Project readme with setup instructions
├── requirements.txt             # All Python dependencies
├── setup.py                     # Package setup for CLI entry point
├── .env.example                 # Template for environment variables
│
├── infra/                       # Infrastructure provisioning (boto3)
│   ├── __init__.py
│   ├── provision.py             # Creates all AWS resources
│   ├── teardown.py              # Destroys all AWS resources (cleanup)
│   └── config.py                # Centralized config (region names, bucket names, etc.)
│
├── lambdas/                     # Lambda function source code
│   ├── replicator/
│   │   ├── handler.py           # S3 event → cross-region copy
│   │   └── requirements.txt     # Lambda-specific deps (if any beyond boto3)
│   └── healthchecker/
│       ├── handler.py           # Inventory comparison + SNS alert
│       └── requirements.txt
│
├── cli/                         # CLI tool
│   ├── __init__.py
│   ├── main.py                  # Click CLI entry point
│   ├── commands/
│   │   ├── __init__.py
│   │   ├── upload.py            # Upload files to primary bucket
│   │   ├── sync.py              # Manual full sync primary → backup
│   │   ├── status.py            # Show replication log from DynamoDB
│   │   ├── drift.py             # Run drift detection manually
│   │   ├── failover.py          # Swap primary/backup roles
│   │   └── seed.py              # Generate and upload fake NGO data
│   └── utils.py                 # Shared helpers (S3 client, table ref, etc.)
│
├── dashboard/                   # Streamlit dashboard
│   ├── app.py                   # Main Streamlit app
│   ├── pages/
│   │   ├── overview.py          # Replication health summary
│   │   ├── objects.py           # Object inventory browser
│   │   ├── logs.py              # Replication log viewer
│   │   └── controls.py         # Manual sync/failover triggers
│   └── components/
│       ├── metrics.py           # Metric cards (total objects, sync %, last sync)
│       └── charts.py            # Replication timeline, drift chart
│
├── tests/                       # Unit + integration tests
│   ├── test_replicator.py
│   ├── test_healthchecker.py
│   ├── test_cli.py
│   └── test_infra.py
│
├── scripts/
│   ├── deploy_lambdas.sh        # Zip + upload lambdas to AWS
│   └── run_dashboard.sh         # Launch Streamlit
│
├── data/                        # Sample/fake NGO data
│   └── sample_files/
│       ├── donor_records.csv
│       ├── field_report_2024.pdf    # (small placeholder)
│       ├── beneficiary_data.json
│       └── project_budget.xlsx      # (small placeholder)
│
└── docs/
    ├── architecture.md          # Architecture explanation
    └── pdc_relevance.md         # How this relates to distributed systems concepts
```

---

## 4. Detailed Implementation

### 4.1 `infra/config.py` — Centralized Configuration

```python
import os
from dataclasses import dataclass

@dataclass
class Config:
    # Unique suffix to avoid S3 naming collisions (set via .env or generate)
    project_id: str = os.getenv("RESCUE_PROJECT_ID", "team01")
    
    primary_region: str = "us-east-1"
    backup_region: str = "eu-west-1"
    
    @property
    def primary_bucket(self) -> str:
        return f"rescue-primary-{self.project_id}"
    
    @property
    def backup_bucket(self) -> str:
        return f"rescue-backup-{self.project_id}"
    
    dynamo_table: str = "rescue-replication-log"
    sns_topic_name: str = "rescue-alerts"
    
    replicator_lambda_name: str = "rescue-replicator"
    healthchecker_lambda_name: str = "rescue-healthchecker"
    
    lambda_runtime: str = "python3.12"
    lambda_timeout: int = 60  # seconds
    lambda_memory: int = 256  # MB
    
    healthcheck_schedule: str = "rate(15 minutes)"
    
    alert_email: str = os.getenv("RESCUE_ALERT_EMAIL", "")

config = Config()
```

### 4.2 `infra/provision.py` — Full Infrastructure Setup

This script must create ALL AWS resources using boto3. It should be **idempotent** — safe to run multiple times without duplicating resources.

**Resources to create (in order):**

1. **S3 Buckets**
   - Primary bucket in `us-east-1` with versioning enabled
   - Backup bucket in `eu-west-1` with versioning enabled
   - Both buckets: block all public access, enable server-side encryption (AES-256)

2. **DynamoDB Table** (`rescue-replication-log` in `us-east-1`)
   - Partition key: `object_key` (String)
   - Sort key: `timestamp` (String, ISO 8601)
   - Attributes: `status` (PENDING/SUCCESS/FAILED), `size_bytes`, `checksum_sha256`, `source_region`, `dest_region`, `error_message`
   - Billing: PAY_PER_REQUEST (free tier compatible)
   - TTL attribute: `expiry_ttl` (auto-expire logs older than 30 days)

3. **IAM Role for Lambda** (`rescue-lambda-role`)
   - Trust policy: allow `lambda.amazonaws.com` to assume
   - Inline policy with permissions for:
     - `s3:GetObject`, `s3:PutObject`, `s3:ListBucket` on both buckets
     - `dynamodb:PutItem`, `dynamodb:Query`, `dynamodb:Scan` on the replication log table
     - `sns:Publish` on the alerts topic
     - `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` for CloudWatch Logs
   - **Principle of least privilege**: scope every permission to exact resource ARNs, never use wildcards on resources

4. **SNS Topic** (`rescue-alerts` in `us-east-1`)
   - Create topic
   - Subscribe the configured alert email (requires manual confirmation via email)

5. **Lambda Functions** (both in `us-east-1`)
   - **Replicator**:
     - Package `lambdas/replicator/handler.py` into a zip
     - Create function with the IAM role, 256MB memory, 60s timeout
     - Add S3 event notification on primary bucket for `s3:ObjectCreated:*` and `s3:ObjectRemoved:*` events
     - Add required permission for S3 to invoke Lambda
   - **HealthChecker**:
     - Package `lambdas/healthchecker/handler.py` into a zip
     - Create function with the IAM role
     - Create CloudWatch Events rule with schedule expression `rate(15 minutes)`
     - Add rule as event source for this Lambda
     - Add required permission for CloudWatch Events to invoke Lambda

6. **Print/return a summary** of all created resource ARNs

**Important provisioning details:**
- Wait for IAM role propagation (sleep 10 seconds after role creation before creating Lambdas)
- Use `try/except` with `ClientError` to skip creation if resource already exists
- Tag every resource with `Project: aws-rescue` and `Environment: dev`

### 4.3 `infra/teardown.py` — Clean Destruction

Delete all resources in reverse order:
1. Remove S3 event notifications
2. Delete Lambda functions
3. Delete CloudWatch Events rules
4. Delete SNS subscriptions and topic
5. Delete DynamoDB table
6. Empty and delete both S3 buckets (must delete all objects + versions first)
7. Detach and delete IAM policies, then delete IAM role

Print confirmation for each resource destroyed. Handle "resource not found" gracefully.

### 4.4 `lambdas/replicator/handler.py` — Cross-Region Replication

```
Event flow: S3 ObjectCreated/ObjectRemoved → Lambda trigger → handler
```

**Logic:**
1. Parse the S3 event records from `event['Records']`
2. For each record:
   - Extract bucket name, object key, event type, object size
   - If event is `ObjectCreated`:
     a. Get the object from primary bucket (use source region client)
     b. Compute SHA-256 checksum of the object body
     c. Put the object into backup bucket (use destination region client) with same key, metadata, and content type
     d. Verify the copy by checking ETag or doing a HeadObject on backup
     e. Log to DynamoDB: `{object_key, timestamp, status: "SUCCESS", size_bytes, checksum_sha256, source_region, dest_region}`
   - If event is `ObjectRemoved`:
     a. Delete the corresponding object from backup bucket
     b. Log to DynamoDB with status "DELETED"
   - On any exception:
     a. Log to DynamoDB with status "FAILED" and `error_message`
     b. Publish failure alert to SNS with object key and error details
     c. Re-raise so Lambda retries (up to 2 retries)
3. Return a summary of processed records

**Critical requirements:**
- Use **streaming copy** for large files (don't load entire file into memory). Use `S3.copy()` or `S3.copy_object()` with `CopySource` where possible — this is server-side and doesn't stream through Lambda. For the checksum, do a HeadObject with ChecksumAlgorithm after copy.
- Set `DEST_BUCKET` and `DEST_REGION` as Lambda environment variables (set during provisioning)
- Handle the case where the object was deleted between event firing and Lambda execution (object no longer exists)

### 4.5 `lambdas/healthchecker/handler.py` — Drift Detection

**Logic:**
1. List all objects in primary bucket (paginate with `list_objects_v2`)
2. List all objects in backup bucket (paginate)
3. Build sets of `(key, ETag, size)` tuples for each
4. Compute:
   - `missing_in_backup`: objects in primary but not in backup
   - `missing_in_primary`: objects in backup but not in primary (orphans)
   - `mismatched`: objects in both but with different ETag/size (checksum mismatch)
   - `in_sync_count`: matching objects
5. Calculate `sync_percentage = in_sync / total_primary * 100`
6. If any drift found:
   - Publish detailed alert to SNS including counts and first 10 affected keys
   - Log each drifted object to DynamoDB with status "DRIFT_DETECTED"
7. If all in sync:
   - Log a single "HEALTH_CHECK_OK" entry to DynamoDB
8. Return a JSON summary:
   ```json
   {
     "timestamp": "...",
     "primary_count": 42,
     "backup_count": 42,
     "in_sync": 42,
     "missing_in_backup": 0,
     "missing_in_primary": 0,
     "mismatched": 0,
     "sync_percentage": 100.0,
     "status": "HEALTHY"
   }
   ```

### 4.6 CLI — `cli/main.py` using Click

Entry point: `rescue-cli` (configure in `setup.py` as console script)

**Commands:**

#### `rescue-cli seed`
Generate fake NGO data files and upload them to the primary bucket.
- Generate 10-20 fake files: CSVs (donor lists, beneficiary data), JSON (project metadata, grant applications), TXT (field reports)
- Use `faker` library for realistic NGO-domain data (names, locations, amounts, dates)
- Each file 1-50 KB (stay well within free tier)
- Upload all to primary bucket under organized prefixes: `donors/`, `reports/`, `projects/`, `finance/`
- Print progress with file names and sizes

#### `rescue-cli upload <file_path> [--prefix PREFIX]`
Upload a local file to the primary bucket.
- Optional prefix for S3 key organization
- Print confirmation with S3 URI
- The Lambda replicator handles the rest automatically

#### `rescue-cli sync [--dry-run]`
Force a full manual sync from primary → backup.
- List all objects in primary
- For each, check if backup has matching key+ETag
- Copy any missing or mismatched objects
- `--dry-run`: only report what would be synced, don't copy
- Show a progress bar (use `tqdm` or `click.progressbar`)
- Print summary: X synced, Y already in sync, Z failed

#### `rescue-cli status [--limit N]`
Show recent replication log entries from DynamoDB.
- Query last N entries (default 20), sorted by timestamp descending
- Display as a formatted table (use `tabulate` or `rich`)
- Columns: Timestamp | Object Key | Status | Size | Source → Dest

#### `rescue-cli drift`
Run drift detection (same logic as HealthChecker Lambda, but locally).
- Compare primary and backup inventories
- Print a detailed drift report
- Color-code: green for in-sync, red for missing, yellow for mismatched
- Use `rich` for terminal formatting

#### `rescue-cli failover [--confirm]`
Simulate a failover scenario: swap the roles of primary and backup.
- Update config to point to backup as new primary
- Optionally trigger a reverse sync
- Require `--confirm` flag (destructive action)
- Print step-by-step failover log

#### `rescue-cli dashboard`
Convenience command to launch the Streamlit dashboard.
- Runs `streamlit run dashboard/app.py`

### 4.7 Streamlit Dashboard — `dashboard/app.py`

Use `streamlit` with a multi-page layout. The dashboard reads directly from AWS using boto3.

**Page: Overview (`pages/overview.py`)**
- Top row: 4 metric cards
  - Total Objects (primary)
  - Sync Percentage
  - Last Successful Sync (timestamp)
  - Active Alerts count
- Replication timeline chart: line chart showing objects replicated over time (query DynamoDB, group by hour/day)
- Region status indicators: green/red badges for each region's bucket accessibility (try HeadBucket)
- Auto-refresh toggle (re-run every 30 seconds)

**Page: Object Browser (`pages/objects.py`)**
- Two-column layout: Primary bucket objects (left) | Backup bucket objects (right)
- Each shows: key, size (human-readable), last modified, ETag
- Color coding: green if object exists in both, red if missing from one side
- Search/filter bar for object keys
- Click an object key to see its replication history from DynamoDB

**Page: Replication Logs (`pages/logs.py`)**
- Full DynamoDB log table with filters:
  - Status filter (SUCCESS / FAILED / DRIFT_DETECTED / DELETED)
  - Date range filter
  - Object key search
- Download logs as CSV button
- Show error messages for failed replications

**Page: Controls (`pages/controls.py`)**
- "Trigger Full Sync" button → runs the same logic as `rescue-cli sync`
- "Run Health Check" button → invokes HealthChecker Lambda directly via boto3 `lambda.invoke()`
- "Simulate Outage" button → temporarily block access to primary bucket (for demo purposes, just disable the Lambda trigger) and show that backup remains accessible
- "Seed Test Data" button → upload fake files (same as CLI seed command)
- Each action shows a spinner while running and success/error toast on completion

**Styling:**
- Use `st.set_page_config(page_title="AWS-RESCUE", page_icon="🛡️", layout="wide")`
- Consistent color scheme: use st.columns and st.metrics for clean layout
- Sidebar: project name, current config (regions, bucket names), refresh button

### 4.8 Sample Data Generation (`cli/commands/seed.py`)

Generate realistic fake NGO data using the `faker` library:

- `donors/donor_list_YYYY.csv` — columns: donor_id, name, email, country, donation_amount, currency, date, recurring (bool)
- `donors/major_donors.json` — top donors with contact details and giving history
- `reports/field_report_{country}_{date}.txt` — 2-3 paragraphs of realistic field report text
- `reports/quarterly_summary_Q{n}_{year}.csv` — program metrics by region
- `projects/active_projects.json` — project name, country, budget, start/end dates, status
- `projects/grant_application_{id}.json` — grant details with budget breakdown
- `finance/monthly_expenses_{month}_{year}.csv` — expense line items with categories
- `finance/annual_budget_{year}.json` — budget allocations by department

Generate 15-20 files total, each under 50KB. Use realistic NGO countries (Kenya, Bangladesh, Colombia, etc.) and program types (Water & Sanitation, Education, Health, Livelihoods).

---

## 5. Dependencies

### `requirements.txt`
```
boto3>=1.34.0
click>=8.1.0
streamlit>=1.30.0
faker>=22.0.0
rich>=13.0.0
tabulate>=0.9.0
tqdm>=4.66.0
python-dotenv>=1.0.0
pandas>=2.1.0
plotly>=5.18.0
```

### `setup.py`
```python
from setuptools import setup, find_packages

setup(
    name="aws-rescue",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[...],  # from requirements.txt
    entry_points={
        "console_scripts": [
            "rescue-cli=cli.main:cli",
        ],
    },
)
```

---

## 6. Environment Setup

### `.env.example`
```bash
# AWS credentials (use `aws configure` or set these)
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_DEFAULT_REGION=us-east-1

# Project-specific
RESCUE_PROJECT_ID=team01          # Unique suffix for resource names
RESCUE_ALERT_EMAIL=team@example.com  # Email for SNS alerts
```

---

## 7. PDC / Distributed Systems Relevance

Create `docs/pdc_relevance.md` explaining how this project maps to distributed systems concepts covered in CS432:

| PDC Concept | How AWS-RESCUE Uses It |
|---|---|
| **Data Replication** | Cross-region S3 mirroring is active replication — every write to primary is propagated to backup. This is eventual consistency in action. |
| **Fault Tolerance** | The backup region survives independently if the primary region goes down. The failover command demonstrates recovery. |
| **Consistency Models** | S3 provides strong read-after-write consistency within a region, but cross-region replication is eventually consistent. The drift checker measures this gap. |
| **Event-Driven Architecture** | S3 events trigger Lambda — this is asynchronous message passing between distributed components. |
| **Health Monitoring** | The periodic health checker is a heartbeat/polling mechanism, similar to failure detectors in distributed systems. |
| **Mutual Exclusion** | DynamoDB conditional writes could be used to prevent duplicate replication of the same object (compare to distributed locking). |
| **Clock Synchronization** | All timestamps use ISO 8601 UTC — a shared clock reference. The health checker compares timestamps across regions (related to Lamport/vector clock concepts). |
| **Consensus** | The drift checker determines system state by comparing two distributed sources of truth (primary vs backup inventory). |
| **Scalability** | Lambda auto-scales with event volume. S3 handles unlimited objects. No single bottleneck. |

---

## 8. Testing Strategy

### `tests/test_replicator.py`
- Mock S3 events using sample event JSON
- Use `moto` library to mock AWS services
- Test: object created → appears in backup bucket
- Test: object deleted → removed from backup
- Test: replication failure → DynamoDB logs FAILED status
- Test: large object handling (streaming copy)

### `tests/test_healthchecker.py`
- Mock two S3 buckets with known contents
- Test: identical buckets → HEALTHY status, 100% sync
- Test: missing object in backup → correctly identified
- Test: mismatched ETag → flagged as drift
- Test: empty buckets → handled gracefully

### `tests/test_cli.py`
- Test seed command generates expected number of files
- Test upload command puts file in correct S3 location
- Test status command formats DynamoDB results correctly
- Test drift command output matches expected format

### `tests/test_infra.py`
- Test provision is idempotent (run twice, no errors)
- Test teardown removes all resources
- Test IAM policy has correct permissions

Use `pytest` as test runner. Use `moto` for AWS mocking (no real AWS calls in tests).

---

## 9. Build & Run Instructions

**Include these in `README.md`:**

```bash
# 1. Clone and install
git clone <repo>
cd aws-rescue
pip install -e ".[dev]"  # or: pip install -r requirements.txt

# 2. Configure AWS credentials
cp .env.example .env
# Edit .env with your AWS credentials and project ID
# OR use: aws configure

# 3. Provision infrastructure
python -m infra.provision
# Creates: 2 S3 buckets, DynamoDB table, IAM role, 2 Lambdas, SNS topic
# Note: Confirm the SNS email subscription manually

# 4. Seed test data
rescue-cli seed
# Generates and uploads ~20 fake NGO files to primary bucket

# 5. Verify replication
rescue-cli status
rescue-cli drift

# 6. Launch dashboard
rescue-cli dashboard
# OR: streamlit run dashboard/app.py

# 7. Teardown (when done)
python -m infra.teardown
```

---

## 10. Implementation Order

Follow this sequence. Each step should be fully working before moving to the next.

1. **`infra/config.py`** — get config object working
2. **`infra/provision.py`** — create all AWS resources, test by running it
3. **`lambdas/replicator/handler.py`** — implement and deploy, test by uploading a file to S3 via console
4. **`lambdas/healthchecker/handler.py`** — implement and deploy, test by invoking via console
5. **`cli/commands/seed.py`** — generate fake data, upload to primary, verify replication works end-to-end
6. **`cli/main.py` + remaining commands** — upload, sync, status, drift, failover
7. **`dashboard/`** — build Streamlit pages one by one (overview → objects → logs → controls)
8. **`tests/`** — add tests with moto mocks
9. **`infra/teardown.py`** — clean destruction script
10. **`docs/`** — architecture doc and PDC relevance writeup
11. **`scripts/`** — deploy and run helper scripts
12. **`README.md`** — complete project documentation

---

## 11. Critical Implementation Notes

- **Never use `s3:*` or `*` resource wildcards in IAM policies.** Scope every permission to exact bucket ARNs and table ARNs.
- **Lambda deployment**: zip the handler.py file (and any deps), upload via `lambda.create_function()` with `ZipFile` parameter. For updates, use `lambda.update_function_code()`.
- **S3 event notification**: use `s3.put_bucket_notification_configuration()` on the primary bucket. Must also add Lambda invoke permission via `lambda.add_permission()`.
- **Region-aware boto3 clients**: always create separate `boto3.client('s3', region_name=...)` for primary and backup regions. Never rely on default region for cross-region operations.
- **Free tier guardrails**: total data should stay under 2GB across both buckets. Each fake file should be under 50KB. Add a size check in the upload command warning if approaching limits.
- **Error handling everywhere**: wrap all AWS calls in try/except with `botocore.exceptions.ClientError`. Log meaningful error messages.
- **No hardcoded credentials**: use environment variables, `.env` file, or AWS CLI profile. Never commit credentials.
- **All timestamps in UTC ISO 8601**: `datetime.utcnow().isoformat() + "Z"`
