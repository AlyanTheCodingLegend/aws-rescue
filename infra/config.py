import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    project_id: str = field(default_factory=lambda: os.getenv("RESCUE_PROJECT_ID", "rescue42"))

    primary_region: str = "us-east-1"
    backup_region: str = "eu-west-1"

    # These can be swapped by the failover command (rewrites this file)
    _primary_is_original: bool = True

    # Runtime-only flag: temporarily promotes the backup region while a
    # simulated outage is active. Never persisted to disk.
    outage_active: bool = False

    @property
    def _effective_original(self) -> bool:
        # XOR: outage flips the active roles on top of any persisted failover.
        return self._primary_is_original ^ self.outage_active

    @property
    def primary_bucket(self) -> str:
        if self._effective_original:
            return f"rescue-primary-{self.project_id}"
        return f"rescue-backup-{self.project_id}"

    @property
    def backup_bucket(self) -> str:
        if self._effective_original:
            return f"rescue-backup-{self.project_id}"
        return f"rescue-primary-{self.project_id}"

    @property
    def active_primary_region(self) -> str:
        return self.primary_region if self._effective_original else self.backup_region

    @property
    def active_backup_region(self) -> str:
        return self.backup_region if self._effective_original else self.primary_region

    dynamo_table: str = "rescue-replication-log"
    dynamo_region: str = "us-east-1"

    replicator_lambda_name: str = "rescue-replicator"
    healthchecker_lambda_name: str = "rescue-healthchecker"
    lambda_region: str = "us-east-1"

    lambda_runtime: str = "python3.12"
    lambda_timeout: int = 60
    lambda_memory: int = 256

    healthcheck_schedule: str = "rate(15 minutes)"

    iam_role_name: str = "rescue-lambda-role"
    cloudwatch_rule_name: str = "rescue-healthcheck-schedule"

    tags: dict = field(default_factory=lambda: {"Project": "aws-rescue", "Environment": "dev"})

    FREE_TIER_WARN_BYTES: int = 4 * 1024 * 1024 * 1024  # warn at 4GB (free tier is 5GB)
    MAX_FILE_BYTES: int = 50 * 1024  # 50KB per file for seeded data


config = Config()
