"""Execute migrations in disposable databases and record elapsed time."""

from __future__ import annotations

from pathlib import Path
from time import monotonic

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from planguard.harness.database import TemporaryDatabase, connect


class MigrationExecutionResult(BaseModel):
    """Outcome and wall-clock duration of one isolated migration execution."""

    model_config = ConfigDict(extra="forbid")

    migration_name: str
    success: bool
    duration_seconds: float = Field(ge=0)
    error_type: str | None = None
    error_message: str | None = None


def execute_migration(
    server_dsn: str,
    migration_path: Path,
    *,
    statement_timeout_ms: int = 10_000,
) -> MigrationExecutionResult:
    """Run a SQL file only inside a disposable database.

    The supplied DSN is used to create and remove the temporary database;
    migration SQL is never executed against the database named in that DSN.
    """
    if statement_timeout_ms < 1:
        raise ValueError("statement_timeout_ms must be positive")

    source = migration_path.read_text(encoding="utf-8")
    started = monotonic()

    with TemporaryDatabase(server_dsn) as experiment:
        if experiment.dsn is None:
            raise RuntimeError("Temporary database did not provide a connection DSN")

        try:
            with connect(
                experiment.dsn,
                autocommit=True,
                statement_timeout_ms=statement_timeout_ms,
            ) as connection:
                if source.strip():
                    # Simple-query mode supports migration files containing
                    # multiple SQL statements and transaction-control commands.
                    connection.execute(source, prepare=False)
        except (psycopg.Error, ValueError) as error:
            return MigrationExecutionResult(
                migration_name=migration_path.name,
                success=False,
                duration_seconds=monotonic() - started,
                error_type=type(error).__name__,
                error_message=str(error).splitlines()[0][:500],
            )

    return MigrationExecutionResult(
        migration_name=migration_path.name,
        success=True,
        duration_seconds=monotonic() - started,
    )
