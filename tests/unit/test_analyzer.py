"""Focused smoke tests for each registered Phase 2 migration rule."""

import pytest

from planguard.analyzer import analyze_migration
from planguard.analyzer.registry import RULES, get_rule

RULE_CASES = [
    pytest.param(
        "CREATE TABLE users (id integer); CREATE INDEX users_id_idx ON users (id);",
        "PG001",
        id="regular-index-creation",
    ),
    pytest.param(
        "CREATE TABLE users (id integer PRIMARY KEY); "
        "CREATE TABLE orders (user_id integer); "
        "ALTER TABLE orders ADD CONSTRAINT orders_user_fk "
        "FOREIGN KEY (user_id) REFERENCES users (id);",
        "PG002",
        id="validated-foreign-key",
    ),
    pytest.param(
        "CREATE TABLE users (age integer); "
        "ALTER TABLE users ADD CONSTRAINT users_age_check CHECK (age >= 0);",
        "PG003",
        id="validated-check-constraint",
    ),
    pytest.param(
        "CREATE TABLE users (email text); ALTER TABLE users ALTER COLUMN email SET NOT NULL;",
        "PG004",
        id="set-not-null",
    ),
    pytest.param(
        "CREATE TABLE events (created_at timestamptz DEFAULT clock_timestamp());",
        "PG005",
        id="volatile-default",
    ),
    pytest.param(
        "CREATE TABLE users (age integer); ALTER TABLE users ALTER COLUMN age TYPE text;",
        "PG006",
        id="type-change",
    ),
    pytest.param(
        "CREATE TABLE users (id integer, email text); ALTER TABLE users DROP COLUMN email;",
        "PG007",
        id="drop-column",
    ),
    pytest.param(
        "CREATE TABLE users (email text); "
        "ALTER TABLE users ADD CONSTRAINT users_email_unique UNIQUE (email);",
        "PG008",
        id="unique-constraint",
    ),
    pytest.param(
        "CREATE TABLE users (id integer); CREATE INDEX users_id_idx ON users (id); "
        "DROP INDEX users_id_idx;",
        "PG009",
        id="non-concurrent-index-drop",
    ),
    pytest.param(
        "CREATE TABLE users (id integer); VACUUM FULL users;",
        "PG010",
        id="maintenance-operation",
    ),
    pytest.param(
        "BEGIN; CREATE INDEX CONCURRENTLY users_id_idx ON users (id); COMMIT;",
        "PG011",
        id="concurrent-index-inside-transaction",
    ),
    pytest.param(
        "BEGIN; CREATE TABLE users (id integer); ALTER TABLE users ADD COLUMN email text; COMMIT;",
        "PG012",
        id="multiple-ddl-locks-in-transaction",
    ),
    pytest.param(
        "CREATE TABLE users (id integer); ALTER TABLE users ADD COLUMN email text;",
        "PG013",
        id="missing-lock-timeout",
    ),
    pytest.param(
        "CREATE TABLE users (id integer PRIMARY KEY); "
        "CREATE TABLE orders (user_id integer, "
        "CONSTRAINT orders_user_fk FOREIGN KEY (user_id) REFERENCES users (id));",
        "PG014",
        id="foreign-key-without-supporting-index",
    ),
]


@pytest.mark.parametrize(("sql", "expected_rule"), RULE_CASES)
def test_rule_reports_its_expected_finding(sql: str, expected_rule: str) -> None:
    findings = analyze_migration(sql, pg_version=16)

    assert expected_rule in {finding.rule_id for finding in findings}


def test_registry_contains_all_fourteen_phase_two_rules() -> None:
    rule_ids = [rule.rule_id for rule in RULES]

    assert rule_ids == [f"PG{number:03}" for number in range(1, 15)]
    for rule_id in rule_ids:
        assert get_rule(rule_id).rule_id == rule_id


def test_unknown_rule_id_raises_key_error() -> None:
    with pytest.raises(KeyError, match="unknown PlanGuard rule"):
        get_rule("PG999")
