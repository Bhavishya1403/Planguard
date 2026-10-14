"""Deterministic synthetic values and schema-aware table seeding helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import psycopg
from psycopg import sql
from psycopg.types.json import Json, Jsonb

from planguard.harness.database import validate_database_identifier


@dataclass(frozen=True)
class ColumnProfile:
    """The subset of a table column's PostgreSQL schema needed for synthetic values."""

    name: str
    data_type: str
    nullable: bool = False
    has_default: bool = False
    choices: tuple[str, ...] = ()
    character_maximum_length: int | None = None
    numeric_precision: int | None = None
    numeric_scale: int | None = None

    @property
    def insertable(self) -> bool:
        """Whether inserts should provide a value instead of using the server default."""
        return not self.has_default


@dataclass(frozen=True)
class TableProfile:
    """A schema-qualified table and its ordered column generation profile."""

    schema: str
    name: str
    columns: tuple[ColumnProfile, ...]


def synthetic_value(column: ColumnProfile, row_index: int, *, seed: int = 0) -> Any:
    """Generate a stable value based on a column type, row position, and seed."""
    if row_index < 0:
        raise ValueError("row_index must be nonnegative")
    if column.choices:
        return column.choices[(row_index + seed) % len(column.choices)]

    normalized_type = column.data_type.casefold()
    name = column.name.casefold()
    integer_maxima = {
        "smallint": 32_767,
        "int2": 32_767,
        "integer": 2_147_483_647,
        "int4": 2_147_483_647,
        "bigint": 9_223_372_036_854_775_807,
        "int8": 9_223_372_036_854_775_807,
    }
    if normalized_type in integer_maxima:
        return 1 + ((row_index + seed) % integer_maxima[normalized_type])
    if normalized_type in {"numeric", "decimal", "real", "double precision", "money"}:
        if column.numeric_precision is not None:
            limit = 10**column.numeric_precision
            if limit <= 1:
                return Decimal(0)
            scaled_value = 1 + ((row_index + seed) % (limit - 1))
            return Decimal(scaled_value).scaleb(-(column.numeric_scale or 0))
        cents = 100 + ((row_index + seed) % 10_000)
        return Decimal(cents) / Decimal(100)
    if normalized_type in {"boolean", "bool"}:
        return (row_index + seed) % 2 == 0
    if normalized_type == "uuid":
        return uuid5(NAMESPACE_URL, f"planguard:{seed}:{column.name}:{row_index}")
    if normalized_type == "date":
        return date(2020, 1, 1) + timedelta(days=(row_index + seed) % 3650)
    if normalized_type in {"timestamp without time zone", "timestamp"}:
        return datetime(2020, 1, 1) + timedelta(minutes=(row_index + seed) * 7)
    if normalized_type == "timestamp with time zone":
        return datetime(2020, 1, 1, tzinfo=UTC) + timedelta(minutes=(row_index + seed) * 7)
    if normalized_type == "time without time zone":
        return (datetime.combine(date(2020, 1, 1), time.min) + timedelta(seconds=row_index)).time()
    if normalized_type == "time with time zone":
        return time(
            hour=(row_index // 3600) % 24,
            minute=(row_index // 60) % 60,
            second=row_index % 60,
            tzinfo=UTC,
        )
    if normalized_type == "json":
        return Json({"column": column.name, "row": row_index, "seed": seed})
    if normalized_type == "jsonb":
        return Jsonb({"column": column.name, "row": row_index, "seed": seed})
    if normalized_type == "bytea":
        return f"{seed}:{column.name}:{row_index}".encode()
    if normalized_type == "array":
        return []
    if normalized_type in {"text", "character varying", "character", "varchar", "char", "name"}:
        if "email" in name:
            value = f"user-{row_index}@example.test"
        elif name in {"status", "state"}:
            value = ("pending", "processing", "shipped", "delivered")[(row_index + seed) % 4]
        else:
            value = f"{column.name}_{(row_index + seed) % 1000:04d}"
        if column.character_maximum_length is not None:
            return value[: column.character_maximum_length]
        return value
    if column.nullable:
        return None
    raise ValueError(
        f"no generic synthetic value profile for non-nullable column "
        f"{column.name!r} of type {column.data_type!r}"
    )


def inspect_table(
    connection: psycopg.Connection[Any],
    schema: str,
    table: str,
) -> TableProfile:
    """Read column metadata for one table using parameterized catalog filters."""
    validate_database_identifier(schema)
    validate_database_identifier(table)
    rows = connection.execute(
        "SELECT columns.column_name, columns.data_type, columns.is_nullable, "
        "columns.column_default, columns.is_identity, columns.is_generated, "
        "ARRAY(SELECT enum.enumlabel FROM pg_type AS type "
        "JOIN pg_namespace AS namespace ON namespace.oid = type.typnamespace "
        "JOIN pg_enum AS enum ON enum.enumtypid = type.oid "
        "WHERE namespace.nspname = columns.udt_schema "
        "AND type.typname = columns.udt_name ORDER BY enum.enumsortorder) AS enum_choices, "
        "columns.character_maximum_length, columns.numeric_precision, columns.numeric_scale "
        "FROM information_schema.columns AS columns "
        "WHERE columns.table_schema = %s AND columns.table_name = %s "
        "ORDER BY columns.ordinal_position",
        (schema, table),
    ).fetchall()
    if not rows:
        raise ValueError(f"table {schema}.{table} does not exist or has no visible columns")

    columns = tuple(
        ColumnProfile(
            name=str(row[0]),
            data_type=str(row[1]),
            nullable=str(row[2]) == "YES",
            has_default=row[3] is not None or str(row[4]) == "YES" or str(row[5]) != "NEVER",
            choices=tuple(str(choice) for choice in row[6]),
            character_maximum_length=(int(row[7]) if row[7] is not None else None),
            numeric_precision=(int(row[8]) if row[8] is not None else None),
            numeric_scale=(int(row[9]) if row[9] is not None else None),
        )
        for row in rows
    )
    return TableProfile(schema=schema, name=table, columns=columns)


def seed_table(
    connection: psycopg.Connection[Any],
    profile: TableProfile,
    row_count: int,
    *,
    seed: int = 0,
    batch_size: int = 1000,
) -> int:
    """Insert generated rows with quoted identifiers and parameterized values.

    Transaction ownership remains with the caller; this helper never commits a caller's work.
    """
    validate_database_identifier(profile.schema)
    validate_database_identifier(profile.name)
    if row_count < 0:
        raise ValueError("row_count must be nonnegative")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if row_count == 0:
        return 0

    columns = tuple(column for column in profile.columns if column.insertable)
    table_identifier = sql.Identifier(profile.schema, profile.name)
    if not columns:
        statement = sql.SQL("INSERT INTO {} DEFAULT VALUES").format(table_identifier)
        with connection.cursor() as cursor:
            for _ in range(row_count):
                cursor.execute(statement)
        return row_count

    statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        table_identifier,
        sql.SQL(", ").join(sql.Identifier(column.name) for column in columns),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )
    with connection.cursor() as cursor:
        for batch_start in range(0, row_count, batch_size):
            batch_end = min(row_count, batch_start + batch_size)
            batch = [
                tuple(synthetic_value(column, index, seed=seed) for column in columns)
                for index in range(batch_start, batch_end)
            ]
            cursor.executemany(statement, batch)
    return row_count
