"""Execute SQL workloads in an isolated temporary PostgreSQL database."""

from __future__ import annotations

from time import monotonic

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from planguard.harness.database import TemporaryDatabase, connect
from planguard.workload.metrics import WorkloadResult, measure_workload


class WorkloadRunResult(BaseModel):
    """Outcome of a workload run in an isolated database."""

    model_config = ConfigDict(frozen=True)

    success: bool
    duration_seconds: float = Field(ge=0)
    metrics: WorkloadResult | None = None
    error_type: str | None = None
    error_message: str | None = None


def run_sql_workload(
    server_dsn: str,
    *,
    operation_sql: str,
    operation_count: int,
    setup_sql: str = "",
    template: str | None = None,
    statement_timeout_ms: int = 5_000,
) -> WorkloadRunResult:
    """Run repeated SQL operations in a temporary database.

    The supplied DSN is used to create and remove the temporary database.
    Setup and measured SQL execute only on that temporary database.

    A template can be supplied to clone a prepared database. Without a
    template, setup_sql should create and populate any required tables.

    Failed operations are counted by the metrics collector. Setup or
    connection failures produce an unsuccessful run result.
    """
    if operation_count < 0:
        raise ValueError("operation_count must be non-negative")
    if statement_timeout_ms <= 0:
        raise ValueError("statement_timeout_ms must be positive")
    if not operation_sql.strip():
        raise ValueError("operation_sql must not be empty")

    started = monotonic()

    try:
        with TemporaryDatabase(server_dsn, template=template) as experiment:
            if experiment.dsn is None:
                raise RuntimeError("temporary database has no connection DSN")

            with connect(
                experiment.dsn,
                autocommit=True,
                statement_timeout_ms=statement_timeout_ms,
            ) as connection:
                if setup_sql.strip():
                    connection.execute(setup_sql, prepare=False)

                def operation() -> None:
                    connection.execute(operation_sql, prepare=False)

                metrics = measure_workload(
                    operation,
                    operation_count=operation_count,
                )

        return WorkloadRunResult(
            success=metrics.errors == 0,
            duration_seconds=monotonic() - started,
            metrics=metrics,
        )

    except (psycopg.Error, OSError, RuntimeError, ValueError) as error:
        return WorkloadRunResult(
            success=False,
            duration_seconds=monotonic() - started,
            error_type=type(error).__name__,
            error_message=str(error),
        )
