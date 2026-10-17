"""Tests for concurrent PostgreSQL workload execution."""

from threading import Lock
from unittest.mock import MagicMock, patch

import psycopg
import pytest

from planguard.config import WorkloadConfig
from planguard.workload.concurrent import run_concurrent_workload


def test_runs_reader_and_writer_workers() -> None:
    executed: list[str] = []
    lock = Lock()

    def fake_connect(*args: object, **kwargs: object) -> MagicMock:
        del args, kwargs
        connection = MagicMock()
        connection.__enter__.return_value = connection

        def execute(statement: str, **options: object) -> None:
            del options
            with lock:
                executed.append(statement)

        connection.execute.side_effect = execute
        return connection

    config = WorkloadConfig(
        readers=1,
        writers=1,
        think_time_seconds=0,
    )

    with patch(
        "planguard.workload.concurrent.connect",
        side_effect=fake_connect,
    ) as connect_mock:
        result = run_concurrent_workload(
            "host=127.0.0.1 dbname=experiment",
            config,
            duration_seconds=0.05,
            reader_sql="SELECT 1;",
            insert_sql="INSERT INTO orders VALUES (1);",
            update_sql="UPDATE orders SET id = 1;",
            delete_sql="DELETE FROM orders WHERE id = 1;",
        )

    assert connect_mock.call_count == 2
    assert len(executed) > 0
    assert result.metrics.attempted_operations == len(executed)
    assert result.metrics.successful_operations == len(executed)
    assert result.metrics.errors == 0
    assert result.reader_operations > 0
    assert result.writer_operations > 0


def test_counts_sql_operation_failures() -> None:
    connection = MagicMock()
    connection.__enter__.return_value = connection
    calls = 0

    def execute_once_fails(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise psycopg.ProgrammingError("simulated SQL failure")

    connection.execute.side_effect = execute_once_fails

    config = WorkloadConfig(
        readers=1,
        writers=0,
        think_time_seconds=0,
    )

    with patch(
        "planguard.workload.concurrent.connect",
        return_value=connection,
    ):
        result = run_concurrent_workload(
            "host=127.0.0.1 dbname=experiment",
            config,
            duration_seconds=0.01,
            reader_sql="SELECT 1;",
            insert_sql="INSERT INTO orders VALUES (1);",
            update_sql="UPDATE orders SET id = 1;",
            delete_sql="DELETE FROM orders WHERE id = 1;",
        )

    assert result.metrics.errors >= 1
    assert result.success is False


def test_rejects_invalid_duration_and_no_workers() -> None:
    config = WorkloadConfig(readers=1, writers=0)

    with pytest.raises(ValueError, match="duration_seconds"):
        run_concurrent_workload(
            "unused",
            config,
            duration_seconds=0,
            reader_sql="SELECT 1;",
            insert_sql="INSERT INTO orders VALUES (1);",
            update_sql="UPDATE orders SET id = 1;",
            delete_sql="DELETE FROM orders WHERE id = 1;",
        )

    with pytest.raises(ValueError, match="at least one"):
        run_concurrent_workload(
            "unused",
            WorkloadConfig(readers=0, writers=0),
            duration_seconds=1,
            reader_sql="SELECT 1;",
            insert_sql="INSERT INTO orders VALUES (1);",
            update_sql="UPDATE orders SET id = 1;",
            delete_sql="DELETE FROM orders WHERE id = 1;",
        )


def test_rejects_empty_sql() -> None:
    with pytest.raises(ValueError, match="reader_sql"):
        run_concurrent_workload(
            "unused",
            WorkloadConfig(readers=1, writers=0),
            duration_seconds=1,
            reader_sql=" ",
            insert_sql="INSERT INTO orders VALUES (1);",
            update_sql="UPDATE orders SET id = 1;",
            delete_sql="DELETE FROM orders WHERE id = 1;",
        )


def test_each_worker_uses_configured_statement_timeout() -> None:
    connection = MagicMock()
    connection.__enter__.return_value = connection
    config = WorkloadConfig(
        readers=1,
        writers=0,
        statement_timeout_ms=1234,
        think_time_seconds=0,
    )

    with patch(
        "planguard.workload.concurrent.connect",
        return_value=connection,
    ) as connect_mock:
        run_concurrent_workload(
            "host=127.0.0.1 dbname=experiment",
            config,
            duration_seconds=0.01,
            reader_sql="SELECT 1;",
            insert_sql="INSERT INTO orders VALUES (1);",
            update_sql="UPDATE orders SET id = 1;",
            delete_sql="DELETE FROM orders WHERE id = 1;",
        )

    connect_mock.assert_called_once_with(
        "host=127.0.0.1 dbname=experiment",
        autocommit=True,
        statement_timeout_ms=1234,
    )
