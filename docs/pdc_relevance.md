# PDC Relevance — AWS-RESCUE and Distributed Systems

**Course:** CS432 — Parallel & Distributed Computing

This document maps AWS-RESCUE's design to the distributed systems concepts covered in CS432.

---

## Concept Mapping

| PDC Concept | How AWS-RESCUE Uses It |
|---|---|
| **Data Replication** | Every write to the primary S3 bucket is propagated to the backup bucket via the Replicator Lambda. This is **active replication** — the system proactively maintains a second copy rather than only copying on demand. This mirrors the primary-backup replication model studied in distributed databases. |
| **Fault Tolerance** | The backup region operates independently. If `us-east-1` becomes unavailable, all data remains accessible in `eu-west-1`. The `failover` command demonstrates recovery by redirecting all operations to the backup region without data loss. |
| **Consistency Models** | S3 provides **strong read-after-write consistency** within a single region. Cross-region replication is **eventually consistent** — there is a window between an upload and its replication where the backup is stale. The drift checker measures this inconsistency window by comparing both inventories. |
| **Event-Driven Architecture** | S3 events trigger the Replicator Lambda — this is **asynchronous message passing** between distributed components. The primary bucket and the replicator are decoupled: the bucket does not wait for replication to complete. This is analogous to producer-consumer patterns in distributed message queues. |
| **Health Monitoring & Failure Detection** | The HealthChecker Lambda runs every 15 minutes as a **heartbeat/polling mechanism**. It compares both bucket states and logs drift. This is structurally equivalent to the failure detectors described in the Chandra-Toueg model — a process periodically probing system state and escalating when anomalies are detected. |
| **Mutual Exclusion** | DynamoDB's **conditional writes** (`put_item` with condition expressions) can prevent duplicate replication log entries for the same object and timestamp — analogous to distributed locking or test-and-set operations to prevent race conditions in concurrent systems. |
| **Clock Synchronization** | All timestamps use **ISO 8601 UTC** (`datetime.utcnow()`). Both Lambda functions and the CLI use the same time reference, avoiding clock skew between regions. This relates to **Lamport timestamps** — a shared logical clock reference enabling global ordering of events across distributed nodes. |
| **Consensus / State Agreement** | The drift checker determines **system state by comparing two distributed sources of truth** (primary vs backup inventories). Disagreement between the two is drift. Resolving drift (via manual sync or the Replicator) is analogous to a consensus protocol converging distributed state to a single agreed value. |
| **Scalability** | Lambda **auto-scales** with event volume — 1 upload or 10,000 uploads, the same function handles them. S3 handles unlimited objects with no throughput bottleneck per bucket. There is no single-threaded orchestrator; each S3 event is an independent unit of work processed in parallel. |
| **Partitioning & CAP Theorem** | AWS-RESCUE demonstrates a **CP-leaning design**: it prioritises consistency (drift detection, checksums) and partition tolerance (backup region survives a primary failure) over availability during the replication window. The manual sync command is the tool for restoring consistency after a partition. |

---

## Key Distributed Systems Properties Demonstrated

### 1. Eventual Consistency in Practice

After an object is uploaded to the primary bucket, there is a delay (typically < 1 second in normal operation) before it appears in the backup. During this window, a client reading from the backup sees stale state. The `drift` command makes this gap observable and measurable — a concrete demonstration of eventual consistency that is often discussed abstractly in textbooks.

### 2. Independent Failure Domains

The two S3 buckets are in different AWS regions (`us-east-1`, `eu-west-1`). AWS regions are physically isolated with independent power, networking, and control planes. A regional failure cannot cascade to the other region. This is the distributed systems principle of **fault isolation through independent failure domains**.

### 3. Idempotency

Both the `sync` command and the Replicator Lambda are **idempotent**: running them multiple times on the same state produces the same result. Objects already in sync are skipped. This is essential in distributed systems where at-least-once delivery means operations may be retried.

### 4. Observability

DynamoDB acts as a **distributed audit log** — a persistent, queryable record of every replication event with timestamps, status, size, and checksums. The Streamlit dashboard provides real-time observability into system state, analogous to monitoring systems like Prometheus/Grafana in production distributed systems.

---

## Why This Project Fits CS432

AWS-RESCUE is not a toy simulation of distributed concepts — it uses real cloud infrastructure where these concepts have real consequences. S3 cross-region replication experiences actual network latency. Lambda cold starts introduce real timing non-determinism. DynamoDB's eventual consistency model is the same one described in the Amazon Dynamo paper. Students can observe distributed systems behaviour in a live environment rather than reasoning about it abstractly.
