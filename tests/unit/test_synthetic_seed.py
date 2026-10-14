"""Unit tests for deterministic, schema-aware synthetic data generation."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from planguard.seed.synthetic import (
    ColumnProfile,
    TableProfile,
    inspect_table,
    seed_table,
    synthetic_value,
)


@pytest.mark.parametrize(
    ("column", "expected"),
    [
        (ColumnProfile("id", "integer"), 1),
        (ColumnProfile("is_active", "boolean"), True),
        (ColumnProfile("created_on", "date"), date(2020, 1, 1)),
        (ColumnProfile("created_at", "timestamp without time zone"), datetime(2020, 1, 1)),
        (
            ColumnProfile("created_at", "timestamp with time zone"),
            datetime(2020, 1, 1, tzinfo=UTC),
        ),
        (ColumnProfile("price", "numeric"), Decimal("1.00")),
    ],
)
def test_synthetic_values_follow_column_types(column: ColumnProfile, expected: object) -> None:
    """Built-in data types should receive values that PostgreSQL can adapt."""
    assert synthetic_value(column, 0) == expected


def test_synthetic_values_are_deterministic_and_seeded() -> None:
    """The same profile and seed should create repeatable values across trial databases."""
    column = ColumnProfile("status", "text")

    assert synthetic_value(column, 3, seed=9) == synthetic_value(column, 3, seed=9)
    assert synthetic_value(column, 3, seed=9) != synthetic_value(column, 3, seed=10)


def test_synthetic_values_respect_declared_column_widths_and_numeric_bounds() -> None:
    """Fallback profiles should keep text and numbers inside inspected column limits."""
    short_status = ColumnProfile("status", "character varying", character_maximum_length=8)
    narrow_amount = ColumnProfile("amount", "numeric", numeric_precision=4, numeric_scale=2)

    assert synthetic_value(short_status, 1) == "processi"
    assert synthetic_value(narrow_amount, 999) == Decimal("10.00")


def test_integer_fallback_values_stay_in_the_declared_type_range() -> None:
    """Large row indices should wrap inside a signed PostgreSQL integer type's range."""
    assert synthetic_value(ColumnProfile("small_count", "smallint"), 40_000) == 7_234


def test_synthetic_text_profiles_cover_common_semantic_columns() -> None:
    """Email and status fields should get useful synthetic values, not arbitrary bytes."""
    assert synthetic_value(ColumnProfile("email", "character varying"), 4) == (
        "user-4@example.test"
    )
    assert synthetic_value(ColumnProfile("status", "text"), 0) == "pending"


def test_synthetic_value_rejects_unknown_required_types() -> None:
    """Unknown non-nullable data types need an explicit profile instead of invalid guesses."""
    with pytest.raises(ValueError, match="no generic synthetic value profile"):
        synthetic_value(ColumnProfile("custom", "USER-DEFINED"), 0)


def test_nullable_unknown_type_can_use_null_fallback() -> None:
    """Nullable unsupported columns can safely use SQL NULL in generic seed profiles."""
    assert synthetic_value(ColumnProfile("custom", "USER-DEFINED", nullable=True), 0) is None


def test_enum_choices_are_deterministic() -> None:
    """A discovered enum profile should rotate through the server's valid values."""
    column = ColumnProfile("status", "USER-DEFINED", choices=("new", "done"))

    assert synthetic_value(column, 0) == "new"
    assert synthetic_value(column, 1) == "done"
    assert synthetic_value(column, 2, seed=1) == "done"


def test_inspect_table_maps_metadata_to_a_generic_profile() -> None:
    """Catalog rows should expose defaults, lengths, and valid enum values to the fallback."""

    class FakeConnection:
        def execute(
            self,
            query: str,
            parameters: tuple[str, str],
        ) -> FakeConnection:
            assert "information_schema.columns" in query
            assert parameters == ("public", "orders")
            return self

        def fetchall(self) -> list[tuple[object, ...]]:
            return [
                ("id", "bigint", "NO", None, "YES", "NEVER", [], None, None, None),
                (
                    "status",
                    "USER-DEFINED",
                    "NO",
                    None,
                    "NO",
                    "NEVER",
                    ["pending", "shipped"],
                    None,
                    None,
                    None,
                ),
                ("label", "character varying", "NO", None, "NO", "NEVER", [], 12, None, None),
            ]

    profile = inspect_table(FakeConnection(), "public", "orders")  # type: ignore[arg-type]

    assert profile.columns[0].insertable is False
    assert profile.columns[1].choices == ("pending", "shipped")
    assert profile.columns[2].character_maximum_length == 12


def test_seed_table_zero_rows_does_not_require_a_database_connection() -> None:
    """A zero-row seed is a no-op and must not issue a database query."""
    profile = TableProfile("public", "orders", (ColumnProfile("id", "integer"),))

    assert seed_table(None, profile, 0) == 0  # type: ignore[arg-type]


def test_seed_table_rejects_invalid_identifiers_before_database_use() -> None:
    """Unsafe schema and table names should fail before any SQL is built or sent."""
    profile = TableProfile("public; DROP SCHEMA public", "orders", ())

    with pytest.raises(ValueError, match="database identifiers"):
        seed_table(None, profile, 1)  # type: ignore[arg-type]
