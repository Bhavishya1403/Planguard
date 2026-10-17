"""Compare concurrent PostgreSQL workloads before and after a migration."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from planguard.config import WorkloadConfig
from planguard.harness.database import TemporaryDatabase
from planguard.harness.migration import (
    MigrationExecutionResult,
    execute_migration_on_dsn,
)
from planguard.workload.concurrent import (
    ConcurrentWorkloadResult,
    run_concurrent_workload,
)


class ExperimentComparisonResult(BaseModel):
    """Results from baseline and migrated-database workload runs."""

    model_config = ConfigDict(frozen=True)

    migration: MigrationExecutionResult
    baseline: ConcurrentWorkloadResult
    migrated: ConcurrentWorkloadResult | None
    skipped_reason: str | None = None


def compare_migration_workloads(
    server_dsn: str,
    template_database: str,
    migration_path: Path,
    config: WorkloadConfig,
    *,
    duration_seconds: float,
    reader_sql: str,
    insert_sql: str,
    update_sql: str,
    delete_sql: str,
) -> ExperimentComparisonResult:
    """Compare workloads using two clones of the same prepared template."""
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")

    with TemporaryDatabase(server_dsn, template=template_database) as baseline_db:
        if baseline_db.dsn is None:
            raise RuntimeError("Baseline database did not provide a connection DSN")

        baseline = run_concurrent_workload(
            baseline_db.dsn,
            config,
            duration_seconds=duration_seconds,
            reader_sql=reader_sql,
            insert_sql=insert_sql,
            update_sql=update_sql,
            delete_sql=delete_sql,
        )

    if not baseline.success:
        raise ValueError("Baseline workload failed; comparison was aborted.")

    with TemporaryDatabase(server_dsn, template=template_database) as migrated_db:
        if migrated_db.dsn is None:
            raise RuntimeError("Migration database did not provide a connection DSN")

        migration = execute_migration_on_dsn(
            migrated_db.dsn,
            migration_path,
            statement_timeout_ms=config.statement_timeout_ms,
        )

        if not migration.success:
            return ExperimentComparisonResult(
                migration=migration,
                baseline=baseline,
                migrated=None,
                skipped_reason="Migration failed; migrated workload was skipped.",
            )

        migrated = run_concurrent_workload(
            migrated_db.dsn,
            config,
            duration_seconds=duration_seconds,
            reader_sql=reader_sql,
            insert_sql=insert_sql,
            update_sql=update_sql,
            delete_sql=delete_sql,
        )

    return ExperimentComparisonResult(
        migration=migration,
        baseline=baseline,
        migrated=migrated,
    )
