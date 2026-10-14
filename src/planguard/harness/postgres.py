"""PostgreSQL server selection and bounded Podman or Docker container lifecycle."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

import psycopg
from psycopg.conninfo import make_conninfo

from planguard.harness.database import connect

ContainerRuntime = Literal["auto", "podman", "docker"]
RuntimeName = Literal["podman", "docker"]
_PORT_PATTERN = re.compile(r":([0-9]+)$")


class RuntimeUnavailableError(RuntimeError):
    """Raised when the requested PostgreSQL container runtime is unavailable."""


class RuntimeStartupError(RuntimeError):
    """Raised when a PostgreSQL container cannot start within its configured bounds."""


@dataclass(frozen=True)
class DatabaseEndpoint:
    """Bootstrap connection and origin for a selected server.

    Use ``TemporaryDatabase`` to create an experiment target before applying schema changes.
    """

    dsn: str
    runtime: str


def resolve_dsn(
    cli_dsn: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve a CLI-provided ``--dsn`` value before ``PLANGUARD_DSN``."""
    values = os.environ if environ is None else environ
    candidate = cli_dsn if cli_dsn is not None else values.get("PLANGUARD_DSN")
    if candidate is None:
        return None
    resolved = candidate.strip()
    if not resolved:
        raise ValueError("DSN must not be empty")
    return resolved


def detect_runtime(preference: ContainerRuntime = "auto") -> RuntimeName:
    """Choose an installed runtime, preferring Podman when selection is automatic."""
    if preference not in ("auto", "podman", "docker"):
        raise ValueError(f"unsupported container runtime: {preference}")
    candidates: tuple[RuntimeName, ...]
    if preference == "auto":
        candidates = ("podman", "docker")
    else:
        candidates = (preference,)
    for candidate in candidates:
        if shutil.which(candidate) is not None:
            return candidate
    requested = "Podman or Docker" if preference == "auto" else preference
    raise RuntimeUnavailableError(f"no {requested} executable is available on PATH")


class PostgresContainer:
    """Run an isolated PostgreSQL container and remove it on context exit."""

    def __init__(
        self,
        *,
        image: str = "postgres:16",
        runtime: ContainerRuntime = "auto",
        startup_timeout_seconds: float = 60,
    ) -> None:
        if not image or "\x00" in image:
            raise ValueError("image must be a nonempty container image reference")
        if startup_timeout_seconds <= 0:
            raise ValueError("startup_timeout_seconds must be positive")
        self.image = image
        self.runtime_preference = runtime
        self.startup_timeout_seconds = startup_timeout_seconds
        self.runtime: RuntimeName | None = None
        self.name = f"planguard-{uuid4().hex}"
        self.endpoint: DatabaseEndpoint | None = None
        self._created = False

    def __enter__(self) -> DatabaseEndpoint:
        self.runtime = detect_runtime(self.runtime_preference)
        password = uuid4().hex
        environment = os.environ.copy()
        environment["POSTGRES_PASSWORD"] = password
        run_command = [
            self.runtime,
            "run",
            "--detach",
            "--name",
            self.name,
            "--env",
            "POSTGRES_PASSWORD",
            "--publish",
            "127.0.0.1::5432",
            self.image,
        ]
        try:
            subprocess.run(
                run_command,
                check=True,
                capture_output=True,
                text=True,
                timeout=self.startup_timeout_seconds,
                env=environment,
            )
            self._created = True
            port = self._mapped_port()
            dsn = make_conninfo(
                "",
                host="127.0.0.1",
                port=port,
                dbname="postgres",
                user="postgres",
                password=password,
                connect_timeout=2,
            )
            self._wait_until_ready(dsn)
            self.endpoint = DatabaseEndpoint(dsn=dsn, runtime=self.runtime)
            return self.endpoint
        except (OSError, subprocess.SubprocessError, psycopg.Error, RuntimeStartupError) as error:
            try:
                self._cleanup_after_start_error()
            except RuntimeError as cleanup_error:
                error.add_note(f"container cleanup also failed: {cleanup_error}")
            detail = str(error)
            if isinstance(error, subprocess.CalledProcessError) and error.stderr:
                detail = f"{detail}: {error.stderr.strip()}"
            raise RuntimeStartupError(
                f"could not start PostgreSQL with {self.runtime}: {detail}"
            ) from error

    def __exit__(self, *_: object) -> None:
        if self._created and self.runtime is not None:
            self._cleanup()

    def _mapped_port(self) -> int:
        if self.runtime is None:
            raise RuntimeError("container runtime has not been selected")
        deadline = time.monotonic() + self.startup_timeout_seconds
        last_error = "container did not publish a PostgreSQL port"
        while time.monotonic() < deadline:
            try:
                result = subprocess.run(
                    [self.runtime, "port", self.name, "5432/tcp"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                for line in result.stdout.splitlines():
                    match = _PORT_PATTERN.search(line.strip())
                    if match is not None:
                        return int(match.group(1))
                last_error = f"unrecognized port mapping: {result.stdout.strip()!r}"
            except (OSError, subprocess.SubprocessError) as error:
                last_error = str(error)
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
        raise RuntimeStartupError(last_error)

    def _wait_until_ready(self, dsn: str) -> None:
        deadline = time.monotonic() + self.startup_timeout_seconds
        last_error: psycopg.OperationalError | None = None
        while time.monotonic() < deadline:
            try:
                with connect(dsn, autocommit=True, connect_timeout_seconds=2) as connection:
                    connection.execute("SELECT 1")
                return
            except psycopg.OperationalError as error:
                last_error = error
                time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
        detail = f": {last_error}" if last_error is not None else ""
        raise RuntimeStartupError(f"PostgreSQL did not become ready before timeout{detail}")

    def _cleanup_after_start_error(self) -> None:
        if self._created and self.runtime is not None:
            self._cleanup()

    def _cleanup(self) -> None:
        if self.runtime is None:
            return
        stop_error: subprocess.SubprocessError | OSError | None = None
        try:
            subprocess.run(
                [self.runtime, "stop", "--time", "5", self.name],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as error:
            stop_error = error
        try:
            subprocess.run(
                [self.runtime, "rm", "--force", self.name],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as error:
            if stop_error is not None:
                error.add_note(f"container stop also failed: {stop_error}")
            raise RuntimeError(
                f"could not fully remove PostgreSQL container {self.name}"
            ) from error
        self._created = False


@contextmanager
def postgres_server(
    *,
    cli_dsn: str | None = None,
    runtime: ContainerRuntime = "auto",
    image: str = "postgres:16",
    startup_timeout_seconds: float = 60,
    environ: Mapping[str, str] | None = None,
) -> Iterator[DatabaseEndpoint]:
    """Select an external bootstrap server or manage an ephemeral container.

    The yielded DSN identifies the server used to create experiment databases. Schema changes
    belong on databases created by ``TemporaryDatabase``, never on this server database.
    """
    external_dsn = resolve_dsn(cli_dsn, environ=environ)
    if external_dsn is not None:
        yield DatabaseEndpoint(dsn=external_dsn, runtime="external server")
        return
    with PostgresContainer(
        image=image,
        runtime=runtime,
        startup_timeout_seconds=startup_timeout_seconds,
    ) as endpoint:
        yield endpoint
