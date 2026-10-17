"""Unit tests for isolated migration execution."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import psycopg
import pytest

from planguard.harness.migration import execute_migration


def test_successful_migration_runs_on_temporary_database(
    tmp_path: Path,
) -> None:
    migration = tmp_path / "create_table.sql"
    migration.write_text("CREATE TABLE example (id integer);", encoding="utf-8")

    temporary = MagicMock()
    temporary.dsn = "host=127.0.0.1 dbname=planguard_trial"

    connection = MagicMock()
    connection.__enter__.return_value = connection

    with (
        patch("planguard.harness.migration.TemporaryDatabase") as temp_class,
        patch("planguard.harness.migration.connect") as connect_mock,
    ):
        temp_class.return_value.__enter__.return_value = temporary
        connect_mock.return_value.__enter__.return_value = connection

        result = execute_migration("host=localhost dbname=postgres", migration)

    assert result.success is True
    assert result.migration_name == "create_table.sql"
    assert result.error_type is None
    assert result.duration_seconds >= 0
    temp_class.assert_called_once_with("host=localhost dbname=postgres")
    connect_mock.assert_called_once_with(
        temporary.dsn,
        autocommit=True,
        statement_timeout_ms=10_000,
    )
    connection.execute.assert_called_once_with(
        "CREATE TABLE example (id integer);",
        prepare=False,
    )


def test_migration_failure_is_reported_and_temporary_database_is_cleaned(
    tmp_path: Path,
) -> None:
    migration = tmp_path / "broken.sql"
    migration.write_text("THIS IS NOT SQL;", encoding="utf-8")

    temporary = MagicMock()
    temporary.dsn = "host=127.0.0.1 dbname=planguard_trial"

    connection_context = MagicMock()
    connection_context.__enter__.side_effect = psycopg.ProgrammingError(
        "synthetic migration failure"
    )

    with (
        patch("planguard.harness.migration.TemporaryDatabase") as temp_class,
        patch(
            "planguard.harness.migration.connect",
            return_value=connection_context,
        ),
    ):
        temp_class.return_value.__enter__.return_value = temporary

        result = execute_migration("host=localhost dbname=postgres", migration)

    assert result.success is False
    assert result.error_type == "ProgrammingError"
    assert result.error_message == "synthetic migration failure"
    temp_class.return_value.__exit__.assert_called_once()


def test_nonpositive_statement_timeout_is_rejected(tmp_path: Path) -> None:
    migration = tmp_path / "empty.sql"
    migration.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="must be positive"):
        execute_migration(
            "host=localhost dbname=postgres",
            migration,
            statement_timeout_ms=0,
        )
