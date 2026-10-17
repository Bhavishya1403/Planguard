"""Tests for isolated SQL workload execution."""

from unittest.mock import MagicMock, patch

import psycopg
import pytest

from planguard.workload.runner import run_sql_workload


def test_workload_runs_on_temporary_database() -> None:
    temporary = MagicMock()
    temporary.dsn = "host=127.0.0.1 dbname=planguard_trial"

    connection = MagicMock()
    connection.__enter__.return_value = connection

    with (
        patch("planguard.workload.runner.TemporaryDatabase") as temp_class,
        patch("planguard.workload.runner.connect") as connect_mock,
    ):
        temp_class.return_value.__enter__.return_value = temporary
        connect_mock.return_value.__enter__.return_value = connection

        result = run_sql_workload(
            "host=localhost dbname=postgres",
            setup_sql="CREATE TABLE example (id integer);",
            operation_sql="INSERT INTO example VALUES (1);",
            operation_count=3,
        )

    assert result.success is True
    assert result.metrics is not None
    assert result.metrics.attempted_operations == 3
    assert result.metrics.successful_operations == 3
    temp_class.assert_called_once_with(
        "host=localhost dbname=postgres",
        template=None,
    )
    connect_mock.assert_called_once_with(
        temporary.dsn,
        autocommit=True,
        statement_timeout_ms=5_000,
    )
    assert connection.execute.call_count == 4
    connection.execute.assert_any_call(
        "CREATE TABLE example (id integer);",
        prepare=False,
    )
    connection.execute.assert_any_call(
        "INSERT INTO example VALUES (1);",
        prepare=False,
    )


def test_setup_failure_returns_failure_result() -> None:
    temporary = MagicMock()
    temporary.dsn = "host=127.0.0.1 dbname=planguard_trial"

    connection_context = MagicMock()
    connection_context.__enter__.side_effect = psycopg.OperationalError(
        "simulated connection failure"
    )

    with (
        patch("planguard.workload.runner.TemporaryDatabase") as temp_class,
        patch(
            "planguard.workload.runner.connect",
            return_value=connection_context,
        ),
    ):
        temp_class.return_value.__enter__.return_value = temporary

        result = run_sql_workload(
            "host=localhost dbname=postgres",
            operation_sql="SELECT 1;",
            operation_count=1,
        )

    assert result.success is False
    assert result.metrics is None
    assert result.error_type == "OperationalError"
    assert result.error_message == "simulated connection failure"
    temp_class.return_value.__exit__.assert_called_once()


def test_rejects_invalid_arguments() -> None:
    with pytest.raises(ValueError, match="operation_count"):
        run_sql_workload(
            "host=localhost dbname=postgres",
            operation_sql="SELECT 1;",
            operation_count=-1,
        )

    with pytest.raises(ValueError, match="statement_timeout_ms"):
        run_sql_workload(
            "host=localhost dbname=postgres",
            operation_sql="SELECT 1;",
            operation_count=1,
            statement_timeout_ms=0,
        )

    with pytest.raises(ValueError, match="operation_sql"):
        run_sql_workload(
            "host=localhost dbname=postgres",
            operation_sql="  ",
            operation_count=1,
        )


def test_operation_failures_are_counted() -> None:
    temporary = MagicMock()
    temporary.dsn = "host=127.0.0.1 dbname=planguard_trial"

    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.side_effect = [
        None,
        psycopg.ProgrammingError("simulated SQL failure"),
        None,
    ]

    with (
        patch("planguard.workload.runner.TemporaryDatabase") as temp_class,
        patch("planguard.workload.runner.connect") as connect_mock,
    ):
        temp_class.return_value.__enter__.return_value = temporary
        connect_mock.return_value.__enter__.return_value = connection

        result = run_sql_workload(
            "host=localhost dbname=postgres",
            operation_sql="SELECT 1;",
            operation_count=3,
        )

    assert result.success is False
    assert result.metrics is not None
    assert result.metrics.attempted_operations == 3
    assert result.metrics.successful_operations == 2
    assert result.metrics.errors == 1
    temp_class.return_value.__exit__.assert_called_once()
