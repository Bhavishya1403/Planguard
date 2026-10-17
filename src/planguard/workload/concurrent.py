"""Run concurrent PostgreSQL workloads using independent worker connections."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from random import Random
from threading import Lock
from time import monotonic, sleep

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from planguard.config import WorkloadConfig
from planguard.harness.database import connect
from planguard.workload.metrics import WorkloadResult, _percentile


class ConcurrentWorkloadResult(BaseModel):
    """Aggregated result from concurrent reader and writer workers."""

    model_config = ConfigDict(frozen=True)

    success: bool
    duration_seconds: float = Field(ge=0)
    metrics: WorkloadResult
    reader_operations: int = Field(ge=0)
    writer_operations: int = Field(ge=0)
    error_type: str | None = None
    error_message: str | None = None


def run_concurrent_workload(
    experiment_dsn: str,
    config: WorkloadConfig,
    *,
    duration_seconds: float,
    reader_sql: str,
    insert_sql: str,
    update_sql: str,
    delete_sql: str,
) -> ConcurrentWorkloadResult:
    """Run reader and writer threads against a prepared experiment database.

    The caller must supply an isolated experiment DSN and SQL appropriate
    for its schema. Every worker opens its own bounded connection.
    """
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    if config.readers + config.writers == 0:
        raise ValueError("at least one reader or writer is required")

    statements = {
        "reader": reader_sql,
        "insert": insert_sql,
        "update": update_sql,
        "delete": delete_sql,
    }
    for name, statement in statements.items():
        if not statement.strip():
            raise ValueError(f"{name}_sql must not be empty")

    lock = Lock()
    latencies_ms: list[float] = []
    errors = 0
    reader_operations = 0
    writer_operations = 0
    stop_at = monotonic() + duration_seconds

    def worker(kind: str, worker_id: int) -> None:
        nonlocal errors, reader_operations, writer_operations
        rng = Random(worker_id)
        try:
            with connect(
                experiment_dsn,
                autocommit=True,
                statement_timeout_ms=config.statement_timeout_ms,
            ) as connection:
                while monotonic() < stop_at:
                    if kind == "reader":
                        statement = reader_sql
                    else:
                        choice = rng.random()
                        if choice < config.insert_weight:
                            statement = insert_sql
                        elif choice < config.insert_weight + config.update_weight:
                            statement = update_sql
                        else:
                            statement = delete_sql

                    started = monotonic()
                    failed = False
                    try:
                        connection.execute(statement, prepare=False)
                    except psycopg.Error:
                        failed = True
                    elapsed_ms = (monotonic() - started) * 1000

                    with lock:
                        latencies_ms.append(elapsed_ms)
                        if failed:
                            errors += 1
                        elif kind == "reader":
                            reader_operations += 1
                        else:
                            writer_operations += 1

                    if config.think_time_seconds:
                        sleep(config.think_time_seconds)
        except (psycopg.Error, OSError):
            with lock:
                errors += 1

    started = monotonic()
    worker_count = config.readers + config.writers
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(worker, "reader", index) for index in range(config.readers)]
        futures.extend(
            executor.submit(worker, "writer", config.readers + index)
            for index in range(config.writers)
        )
        for future in futures:
            future.result()

    elapsed = monotonic() - started
    attempted = len(latencies_ms)
    successful = reader_operations + writer_operations
    metrics = WorkloadResult(
        attempted_operations=attempted,
        successful_operations=successful,
        elapsed_seconds=elapsed,
        throughput_ops_per_second=successful / elapsed if elapsed > 0 else 0.0,
        latency_p50_ms=_percentile(latencies_ms, 0.50),
        latency_p95_ms=_percentile(latencies_ms, 0.95),
        latency_p99_ms=_percentile(latencies_ms, 0.99),
        latency_mean_ms=sum(latencies_ms) / len(latencies_ms) if latencies_ms else 0.0,
        errors=errors,
    )

    return ConcurrentWorkloadResult(
        success=errors == 0,
        duration_seconds=elapsed,
        metrics=metrics,
        reader_operations=reader_operations,
        writer_operations=writer_operations,
    )
