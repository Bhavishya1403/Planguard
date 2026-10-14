"""Safe PostgreSQL connection and isolated experiment database helpers."""

from __future__ import annotations

import re
import time
from types import TracebackType
from typing import Literal, Self
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

_DATABASE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_DEFAULT_CONNECT_TIMEOUT_SECONDS = 5
_DEFAULT_STATEMENT_TIMEOUT_MS = 10_000


def validate_database_identifier(identifier: str) -> str:
    """Validate an ASCII PostgreSQL identifier before it enters a SQL statement."""
    if not _DATABASE_IDENTIFIER.fullmatch(identifier):
        raise ValueError(
            "database identifiers must start with a letter or underscore and contain "
            "at most 63 ASCII letters, digits, or underscores"
        )
    return identifier


def connect(
    dsn: str,
    *,
    autocommit: bool = False,
    connect_timeout_seconds: int = _DEFAULT_CONNECT_TIMEOUT_SECONDS,
    statement_timeout_ms: int | None = _DEFAULT_STATEMENT_TIMEOUT_MS,
) -> psycopg.Connection[object]:
    """Open a PostgreSQL connection with a bounded connection attempt."""
    if connect_timeout_seconds <= 0:
        raise ValueError("connect_timeout_seconds must be positive")
    if statement_timeout_ms is not None and statement_timeout_ms <= 0:
        raise ValueError("statement_timeout_ms must be positive when provided")
    if statement_timeout_ms is None:
        conninfo = make_conninfo(dsn, connect_timeout=connect_timeout_seconds)
    else:
        existing_options = conninfo_to_dict(dsn).get("options", "")
        conninfo = make_conninfo(
            dsn,
            connect_timeout=connect_timeout_seconds,
            options=f"{existing_options} -c statement_timeout={statement_timeout_ms}".strip(),
        )
    return psycopg.connect(conninfo, autocommit=autocommit)


def database_dsn(server_dsn: str, database_name: str) -> str:
    """Return a connection string for a database on the same server."""
    validate_database_identifier(database_name)
    return make_conninfo(server_dsn, dbname=database_name)


def create_database(
    server_dsn: str,
    database_name: str,
    *,
    template: str | None = None,
) -> None:
    """Create a database, optionally cloning a validated template database."""
    validate_database_identifier(database_name)
    if template is not None:
        validate_database_identifier(template)
        statement = sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
            sql.Identifier(database_name), sql.Identifier(template)
        )
    else:
        statement = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))

    with connect(server_dsn, autocommit=True) as connection:
        connection.execute(statement)


def _drop_owned_database(database: TemporaryDatabase) -> None:
    """Drop only a database whose current context successfully created it."""
    if not database._created_by_this_run:
        raise RuntimeError("refusing to drop a database not created by this run")
    validate_database_identifier(database.name)
    with connect(database.server_dsn, autocommit=True) as connection:
        connection.execute(
            "SELECT pg_terminate_backend(pid) "
            "FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
            (database.name,),
        )
        connection.execute(
            sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database.name))
        )


class TemporaryDatabase:
    """A database created for one experiment and removed only if this instance created it."""

    def __init__(
        self,
        server_dsn: str,
        *,
        name: str | None = None,
        template: str | None = None,
    ) -> None:
        self.server_dsn = server_dsn
        database_name = f"planguard_{uuid4().hex}" if name is None else name
        self.name = validate_database_identifier(database_name)
        if template is not None:
            validate_database_identifier(template)
        self.template = template
        self.dsn: str | None = None
        self._created_by_this_run = False

    def __enter__(self) -> Self:
        create_database(self.server_dsn, self.name, template=self.template)
        self._created_by_this_run = True
        self.dsn = database_dsn(self.server_dsn, self.name)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        del exc_type, exc_value, traceback
        if self._created_by_this_run:
            self._cleanup_with_deadline()
            self._created_by_this_run = False
        return False

    def _cleanup_with_deadline(self) -> None:
        """Retry transient cleanup failures for a short, bounded interval."""
        deadline = time.monotonic() + 10
        last_error: psycopg.Error | None = None
        while time.monotonic() < deadline:
            try:
                _drop_owned_database(self)
                return
            except psycopg.Error as error:
                last_error = error
                time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
        if last_error is not None:
            raise RuntimeError(
                f"could not clean up experiment database {self.name!r}"
            ) from last_error
