"""Unit tests for runtime selection and safe database lifecycle boundaries."""

import subprocess
from collections.abc import Mapping
from unittest.mock import patch

import pytest

from planguard.harness import database, postgres


def test_dsn_argument_takes_precedence_over_environment() -> None:
    """A command-line DSN should override the environment without changing it."""
    assert postgres.resolve_dsn("  postgresql://cli/db  ", environ={"PLANGUARD_DSN": "env"}) == (
        "postgresql://cli/db"
    )
    assert postgres.resolve_dsn(environ={"PLANGUARD_DSN": "postgresql://env/db"}) == (
        "postgresql://env/db"
    )
    assert postgres.resolve_dsn(environ={}) is None


def test_empty_dsn_is_rejected() -> None:
    """An explicitly supplied but blank DSN should not silently start a container."""
    with pytest.raises(ValueError, match="must not be empty"):
        postgres.resolve_dsn("  ", environ={})


def test_external_server_does_not_select_or_start_a_runtime() -> None:
    """External mode should leave the supplied database server lifecycle alone."""
    with patch.object(postgres, "detect_runtime", side_effect=AssertionError("runtime selected")):
        with postgres.postgres_server(
            cli_dsn="postgresql://host/existing",
            environ={},
        ) as endpoint:
            assert endpoint.dsn == "postgresql://host/existing"
            assert endpoint.runtime == "external server"


def test_auto_detect_prefers_podman_to_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Podman is selected first when both supported runtime commands are installed."""
    found: list[str] = []

    def which(command: str) -> str | None:
        found.append(command)
        return f"/usr/bin/{command}"

    monkeypatch.setattr(postgres.shutil, "which", which)

    assert postgres.detect_runtime() == "podman"
    assert found == ["podman"]


@pytest.mark.parametrize("runtime", ["podman", "docker"])
def test_explicit_runtime_override_uses_only_requested_command(
    monkeypatch: pytest.MonkeyPatch,
    runtime: str,
) -> None:
    """An explicit runtime override should not fall back to another installed runtime."""
    found: list[str] = []

    def which(command: str) -> str | None:
        found.append(command)
        return f"/usr/bin/{command}" if command == runtime else None

    monkeypatch.setattr(postgres.shutil, "which", which)

    assert postgres.detect_runtime(runtime) == runtime
    assert found == [runtime]


def test_requested_runtime_must_be_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unavailable override should be reported instead of silently changed."""
    monkeypatch.setattr(postgres.shutil, "which", lambda _command: None)

    with pytest.raises(postgres.RuntimeUnavailableError, match="docker executable"):
        postgres.detect_runtime("docker")


@pytest.mark.parametrize("identifier", ["x; DROP DATABASE postgres", "a-b", "é", "a" * 64])
def test_database_names_reject_unsafe_identifiers(identifier: str) -> None:
    """Names that are not simple bounded PostgreSQL identifiers must not reach DDL."""
    with pytest.raises(ValueError, match="database identifiers"):
        database.validate_database_identifier(identifier)


def test_database_name_validation_accepts_postgres_identifier_limit() -> None:
    """Validated names should include the longest supported ASCII identifier."""
    identifier = "a" * 63

    assert database.validate_database_identifier(identifier) == identifier


def test_database_dsn_replaces_only_database_name() -> None:
    """Experiment connections should retain server credentials and target the new DB."""
    dsn = database.database_dsn(
        "host=127.0.0.1 user=guard password=secret dbname=source", "trial_1"
    )

    assert "dbname=trial_1" in dsn
    assert "user=guard" in dsn
    assert "password=secret" in dsn


def test_connection_timeout_options_preserve_existing_libpq_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bounded connections should keep caller-supplied options such as search_path."""
    connection_strings: list[str] = []

    def fake_connect(conninfo: str, *, autocommit: bool) -> None:
        del autocommit
        connection_strings.append(conninfo)

    monkeypatch.setattr(database.psycopg, "connect", fake_connect)
    database.connect(
        "host=127.0.0.1 dbname=trial options='-c search_path=custom'",
        statement_timeout_ms=2_000,
    )

    options = database.conninfo_to_dict(connection_strings[0])["options"]
    assert options == "-c search_path=custom -c statement_timeout=2000"


def test_database_creation_failure_never_drops_a_preexisting_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup is disabled until this run's CREATE DATABASE succeeds."""
    drops: list[str] = []

    def create(_dsn: str, _name: str, *, template: str | None = None) -> None:
        del template
        raise RuntimeError("database already exists")

    monkeypatch.setattr(database, "create_database", create)
    monkeypatch.setattr(
        database,
        "_drop_owned_database",
        lambda owned_database: drops.append(owned_database.name),
    )
    temp_database = database.TemporaryDatabase("host=127.0.0.1 dbname=postgres", name="existing")

    with pytest.raises(RuntimeError, match="already exists"):
        temp_database.__enter__()
    temp_database.__exit__(None, None, None)

    assert drops == []


def test_temporary_database_cleans_up_after_body_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successfully created experiment database is removed even when its body raises."""
    dropped_names: list[str] = []
    monkeypatch.setattr(database, "create_database", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        database,
        "_drop_owned_database",
        lambda owned_database: dropped_names.append(owned_database.name),
    )
    temp_database = database.TemporaryDatabase("host=127.0.0.1 dbname=postgres", name="trial_1")

    with pytest.raises(ValueError, match="synthetic body failure"):
        with temp_database:
            raise ValueError("synthetic body failure")

    assert dropped_names == ["trial_1"]


def test_owned_database_drop_refuses_unowned_instances() -> None:
    """The cleanup helper must check its ownership flag before opening a connection."""
    temp_database = database.TemporaryDatabase("host=127.0.0.1 dbname=postgres", name="existing")

    with pytest.raises(RuntimeError, match="not created by this run"):
        database._drop_owned_database(temp_database)


def test_container_lifecycle_uses_bounded_commands_and_removes_its_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Container startup, readiness, and cleanup should all use explicit bounded operations."""
    calls: list[tuple[list[str], int | float | None]] = []
    monkeypatch.setattr(postgres.shutil, "which", lambda command: f"/usr/bin/{command}")

    def run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: int | float,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del check, capture_output, text, env
        calls.append((command, timeout))
        output = "127.0.0.1:54321\n" if command[1] == "port" else ""
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    class FakeConnection:
        def __enter__(self) -> object:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def execute(self, _query: str) -> None:
            return None

    monkeypatch.setattr(postgres.subprocess, "run", run)
    monkeypatch.setattr(
        postgres,
        "connect",
        lambda *_args, **_kwargs: FakeConnection(),
    )

    with postgres.PostgresContainer(startup_timeout_seconds=8) as endpoint:
        assert endpoint.runtime == "podman"
        assert "port=54321" in endpoint.dsn

    assert [call[0][1] for call in calls] == ["run", "port", "stop", "rm"]
    assert all(timeout is not None and timeout <= 10 for _, timeout in calls)
    assert calls[0][0][0] == "podman"
    assert calls[0][0][calls[0][0].index("--publish") + 1] == "127.0.0.1::5432"


def test_container_startup_error_includes_runtime_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed container startup should preserve the runtime's diagnostic text."""
    monkeypatch.setattr(postgres.shutil, "which", lambda command: f"/usr/bin/{command}")

    def fail_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, command, stderr="read-only runtime state")

    monkeypatch.setattr(postgres.subprocess, "run", fail_run)

    with pytest.raises(postgres.RuntimeStartupError, match="read-only runtime state"):
        with postgres.PostgresContainer(startup_timeout_seconds=1):
            pytest.fail("container startup should not complete")


def test_missing_environment_mapping_is_respected() -> None:
    """Tests can resolve DSNs against an injected environment mapping."""
    environment: Mapping[str, str] = {"PLANGUARD_DSN": "postgresql://test/db"}

    assert postgres.resolve_dsn(environ=environment) == "postgresql://test/db"
