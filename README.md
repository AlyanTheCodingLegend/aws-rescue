# AWS-RESCUE

**Resilient Emergency Storage & Cross-region Upload Engine**

Cross-region S3 data mirroring for NGO data survival during regional AWS outages. Replicates objects across two S3 buckets in different AWS regions, monitors replication health, and provides a dashboard + CLI for operations.

**Course:** CS432 — Parallel & Distributed Computing  
**Free Tier:** Stays within AWS Always-Free limits (S3 5 GB, Lambda 1 M invocations, DynamoDB 25 GB)

---

## Architecture

```
PRIMARY (us-east-1)               BACKUP (eu-west-1)
  S3 rescue-primary  ──Lambda──►  S3 rescue-backup
       │                                 │
  S3 event trigger              independently accessible
       │
  Lambda: Replicator  ──────────► DynamoDB (replication log)
  Lambda: HealthChecker (15min schedule)

  Streamlit Dashboard  ──boto3──► both buckets + DynamoDB
  rescue-cli           ──boto3──► both buckets + DynamoDB
```

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
# Or editable install (enables `rescue-cli` command):
pip install -e ".[dev]"
```

### 2. Configure AWS credentials

```bash
cp .env.example .env
# Edit .env — set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, RESCUE_PROJECT_ID
```

Or use `aws configure` if you prefer AWS CLI profiles.

### 3. Provision infrastructure

```bash
python -m infra.provision
```

Creates: 2 S3 buckets (versioned + encrypted), DynamoDB table, IAM role, 2 Lambda functions, CloudWatch schedule.

### 4. Seed test data

```bash
rescue-cli seed
```

Generates ~20 fake NGO files (donors, reports, projects, finance) and uploads them to the primary bucket. The Replicator Lambda replicates each to backup automatically.

### 5. Verify replication

```bash
rescue-cli status          # view DynamoDB replication log
rescue-cli drift           # compare primary vs backup inventories
```

### 6. Launch dashboard

```bash
rescue-cli dashboard
# or: streamlit run dashboard/app.py
# or: bash scripts/run_dashboard.sh
```

Open http://localhost:8501

### 7. Teardown (when done)

```bash
python -m infra.teardown
```

---

## CLI Reference

| Command | Description |
|---|---|
| `rescue-cli seed` | Generate and upload fake NGO data |
| `rescue-cli upload <file> [--prefix PREFIX]` | Upload a file to primary bucket |
| `rescue-cli sync [--dry-run]` | Force full primary → backup sync |
| `rescue-cli status [--limit N]` | View replication log from DynamoDB |
| `rescue-cli drift` | Compare inventories, show drift report |
| `rescue-cli failover [--confirm] [--reverse-sync]` | Swap primary/backup roles |
| `rescue-cli dashboard` | Launch Streamlit dashboard |

---

## Dashboard Pages

| Page | What It Shows |
|---|---|
| **Overview** | Metric cards, replication timeline, region health badges, auto-refresh |
| **Objects** | Side-by-side primary/backup object browser with sync status |
| **Logs** | Filterable DynamoDB log table with CSV download |
| **Controls** | Full sync, health check invocation, outage simulation, seed data |

---

## Failover

The `failover` command rewrites `infra/config.py` to swap which bucket is treated as primary. It does **not** modify `.env`. After failover, restart the CLI and dashboard.

```bash
rescue-cli failover --confirm               # swap roles
rescue-cli failover --confirm --reverse-sync  # swap + sync new-primary → new-backup
```

Run again with `--confirm` to restore original configuration.

---

## Project Structure

```
infra/          AWS resource provisioning (provision.py, teardown.py, config.py)
lambdas/        Lambda source code (replicator/, healthchecker/)
cli/            Click CLI (main.py + commands/)
dashboard/      Streamlit app (app.py, pages/, components/)
data/           Static sample NGO files
scripts/        deploy_lambdas.sh, run_dashboard.sh
docs/           architecture.md, pdc_relevance.md
tests/          Unit tests (moto-based, no real AWS calls)
```

---

## AWS Free Tier Budget

| Service | Free Tier | Usage |
|---|---|---|
| S3 | 5 GB storage, 20K GET, 2K PUT/mo | ~2 MB seed data |
| Lambda | 1 M invocations, 400K GB-sec/mo | event-driven, low volume |
| DynamoDB | 25 GB storage, 25 RCU/WCU | small log table |
| CloudWatch | 1 M API calls/mo | 1 rule, low invocations |

Keep seed files under 50 KB each. Total data stays well under 2 GB.
