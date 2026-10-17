"""Unit tests for migration workload comparison orchestration."""

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from planguard.config import WorkloadConfig
from planguard.harness.experiment import compare_migration_workloads
from planguard.harness.migration import MigrationExecutionResult
from planguard.workload.concurrent import ConcurrentWorkloadResult
from planguard.workload.metrics import WorkloadResult


def make_workload_result() -> ConcurrentWorkloadResult:
    metrics = WorkloadResult(
        attempted_operations=10,
        successful_operations=10,
        elapsed_seconds=1.0,
        throughput_ops_per_second=10.0,
        latency_p50_ms=1.0,
        latency_p95_ms=2.0,
        latency_p99_ms=2.0,
        latency_mean_ms=1.2,
        errors=0,
    )
    return ConcurrentWorkloadResult(
        success=True,
        duration_seconds=1.0,
        metrics=metrics,
        reader_operations=5,
        writer_operations=5,
    )


def make_migration_result(success: bool) -> MigrationExecutionResult:
    return MigrationExecutionResult(
        migration_name="migration.sql",
        success=success,
        duration_seconds=0.1,
        error_type=None if success else "ProgrammingError",
        error_message=None if success else "migration failed",
    )


def test_successful_migration_runs_both_workloads(tmp_path: Path) -> None:
    migration_path = tmp_path / "migration.sql"
    migration_path.write_text("SELECT 1;", encoding="utf-8")
    baseline_db = MagicMock()
    baseline_db.dsn = "dbname=baseline"
    baseline_context = MagicMock()
    baseline_context.__enter__.return_value = baseline_db

    migrated_db = MagicMock()
    migrated_db.dsn = "dbname=migrated"
    migrated_context = MagicMock()
    migrated_context.__enter__.return_value = migrated_db

    with (
        patch(
            "planguard.harness.experiment.TemporaryDatabase",
            side_effect=[baseline_context, migrated_context],
        ) as temporary_database,
        patch(
            "planguard.harness.experiment.run_concurrent_workload",
            return_value=make_workload_result(),
        ) as workload,
        patch(
            "planguard.harness.experiment.execute_migration_on_dsn",
            return_value=make_migration_result(True),
        ) as migration,
    ):
        result = compare_migration_workloads(
            "dbname=server",
            "prepared_template",
            migration_path,
            WorkloadConfig(),
            duration_seconds=1.0,
            reader_sql="SELECT 1;",
            insert_sql="INSERT INTO orders DEFAULT VALUES;",
            update_sql="UPDATE orders SET id = id;",
            delete_sql="DELETE FROM orders WHERE false;",
        )

    assert result.migration.success is True
    assert result.baseline.success is True
    assert result.migrated is not None
    assert result.migrated.success is True
    assert result.skipped_reason is None
    assert temporary_database.call_args_list == [
        call("dbname=server", template="prepared_template"),
        call("dbname=server", template="prepared_template"),
    ]
    assert migration.call_count == 1
    assert workload.call_count == 2
    assert workload.call_args_list[0].args[0] == "dbname=baseline"
    assert workload.call_args_list[1].args[0] == "dbname=migrated"


def test_failed_migration_skips_migrated_workload(tmp_path: Path) -> None:
    migration_path = tmp_path / "migration.sql"
    migration_path.write_text("THIS IS NOT SQL;", encoding="utf-8")
    baseline_db = MagicMock()
    baseline_db.dsn = "dbname=baseline"
    baseline_context = MagicMock()
    baseline_context.__enter__.return_value = baseline_db

    migrated_db = MagicMock()
    migrated_db.dsn = "dbname=migrated"
    migrated_context = MagicMock()
    migrated_context.__enter__.return_value = migrated_db
    with (
        patch(
            "planguard.harness.experiment.TemporaryDatabase",
            side_effect=[baseline_context, migrated_context],
        ),
        patch(
            "planguard.harness.experiment.run_concurrent_workload",
            return_value=make_workload_result(),
        ) as workload,
        patch(
            "planguard.harness.experiment.execute_migration_on_dsn",
            return_value=make_migration_result(False),
        ),
    ):
        result = compare_migration_workloads(
            "dbname=server",
            "prepared_template",
            migration_path,
            WorkloadConfig(),
            duration_seconds=1.0,
            reader_sql="SELECT 1;",
            insert_sql="INSERT INTO orders DEFAULT VALUES;",
            update_sql="UPDATE orders SET id = id;",
            delete_sql="DELETE FROM orders WHERE false;",
        )

    assert result.migration.success is False
    assert result.baseline.success is True
    assert result.migrated is None
    assert result.skipped_reason is not None
    workload.assert_called_once()


def test_failed_baseline_skips_migration_and_migrated_workload(
    tmp_path: Path,
) -> None:
    migration_path = tmp_path / "migration.sql"
    migration_path.write_text("SELECT 1;", encoding="utf-8")

    baseline_db = MagicMock()
    baseline_db.dsn = "dbname=baseline"
    baseline_context = MagicMock()
    baseline_context.__enter__.return_value = baseline_db

    failed_workload = make_workload_result().model_copy(update={"success": False})

    with (
        patch(
            "planguard.harness.experiment.TemporaryDatabase",
            return_value=baseline_context,
        ),
        patch(
            "planguard.harness.experiment.run_concurrent_workload",
            return_value=failed_workload,
        ) as workload,
        patch(
            "planguard.harness.experiment.execute_migration_on_dsn",
        ) as migration,
    ):
        with pytest.raises(ValueError, match="Baseline workload failed"):
            compare_migration_workloads(
                "dbname=server",
                "prepared_template",
                migration_path,
                WorkloadConfig(),
                duration_seconds=1.0,
                reader_sql="SELECT 1;",
                insert_sql="INSERT INTO orders DEFAULT VALUES;",
                update_sql="UPDATE orders SET id = id;",
                delete_sql="DELETE FROM orders WHERE false;",
            )

    migration.assert_not_called()
    workload.assert_called_once()
