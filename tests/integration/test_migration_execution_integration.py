"""Integration tests for isolated migration execution."""

from pathlib import Path

import pytest

from planguard.harness.database import connect
from planguard.harness.migration import execute_migration
from planguard.harness.postgres import postgres_server


@pytest.fixture
def server_dsn():
    """Provide a configured PostgreSQL server or a disposable container."""
    with postgres_server() as endpoint:
        assert endpoint.dsn is not None
        yield endpoint.dsn


def _database_names(server_dsn: str) -> set[str]:
    with connect(server_dsn) as connection:
        rows = connection.execute(
            "SELECT datname FROM pg_database WHERE datname LIKE 'planguard_%'"
        ).fetchall()
    return {row[0] for row in rows}


@pytest.mark.integration
def test_migration_executes_in_isolated_database_and_cleans_up(
    server_dsn: str,
    tmp_path: Path,
) -> None:
    """A successful migration runs in a temporary database that is removed."""
    migration = tmp_path / "create_example.sql"
    migration.write_text(
        """
        CREATE TABLE phase3_example (id integer PRIMARY KEY, note text);
        INSERT INTO phase3_example VALUES (1, 'isolated');
        """,
        encoding="utf-8",
    )

    before = _database_names(server_dsn)
    result = execute_migration(server_dsn, migration)
    after = _database_names(server_dsn)

    assert result.success is True
    assert result.error_type is None
    assert result.duration_seconds >= 0
    assert after == before


@pytest.mark.integration
def test_failed_migration_is_reported_and_database_is_removed(
    server_dsn: str,
    tmp_path: Path,
) -> None:
    """A SQL error is reported and its temporary database is removed."""
    migration = tmp_path / "broken.sql"
    migration.write_text(
        """
        CREATE TABLE phase3_partial (id integer);
        SELECT * FROM table_that_does_not_exist;
        """,
        encoding="utf-8",
    )

    before = _database_names(server_dsn)
    result = execute_migration(server_dsn, migration)
    after = _database_names(server_dsn)

    assert result.success is False
    assert result.error_type is not None
    assert result.error_message is not None
    assert after == before
