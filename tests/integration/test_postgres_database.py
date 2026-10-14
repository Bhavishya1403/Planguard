"""PostgreSQL integration coverage for isolated databases, cloning, and synthetic seeding."""

import os
from collections.abc import Iterator

import pytest

from planguard.harness.database import TemporaryDatabase, connect
from planguard.harness.postgres import (
    PostgresContainer,
    RuntimeStartupError,
    RuntimeUnavailableError,
    resolve_dsn,
)
from planguard.seed.synthetic import inspect_table, seed_table


@pytest.fixture(scope="session")
def server_dsn() -> Iterator[str]:
    """Use the configured server or a managed container as the isolated DB admin server."""
    configured_dsn = resolve_dsn(os.environ.get("PLANGUARD_TEST_DSN"), environ=os.environ)
    if configured_dsn is not None:
        yield configured_dsn
        return

    try:
        container = PostgresContainer(startup_timeout_seconds=45)
        endpoint = container.__enter__()
    except RuntimeUnavailableError as error:
        pytest.skip(str(error))
    except RuntimeStartupError as error:
        pytest.skip(f"container runtime is installed but PostgreSQL cannot start: {error}")
    try:
        yield endpoint.dsn
    finally:
        container.__exit__(None, None, None)


@pytest.mark.integration
def test_isolated_database_can_be_seeded_cloned_and_cleaned(server_dsn: str) -> None:
    """Generated data survives template cloning, and only owned experiment DBs are removed."""
    with connect(server_dsn) as connection:
        source_database = connection.info.dbname
        assert source_database is not None

    template_name: str
    clone_name: str
    with TemporaryDatabase(server_dsn) as template_database:
        template_name = template_database.name
        assert template_database.dsn is not None
        with connect(template_database.dsn, autocommit=True) as connection:
            connection.execute(
                "CREATE TYPE public.order_state AS ENUM ('pending', 'paid', 'shipped')"
            )
            connection.execute(
                "CREATE TABLE public.orders ("
                "id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
                "email text NOT NULL, status public.order_state NOT NULL, "
                "total numeric NOT NULL, created_at timestamp with time zone NOT NULL)"
            )
            profile = inspect_table(connection, "public", "orders")
            assert seed_table(connection, profile, 37, seed=21, batch_size=10) == 37
            assert connection.execute("SELECT count(*) FROM public.orders").fetchone() == (37,)

        with TemporaryDatabase(server_dsn, template=template_database.name) as clone_database:
            clone_name = clone_database.name
            assert clone_database.dsn is not None
            with connect(clone_database.dsn) as connection:
                assert connection.execute("SELECT count(*) FROM public.orders").fetchone() == (37,)
                first_email = connection.execute(
                    "SELECT email FROM public.orders ORDER BY id LIMIT 1"
                ).fetchone()
                assert first_email == ("user-0@example.test",)

        with connect(server_dsn) as connection:
            assert (
                connection.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s", (clone_name,)
                ).fetchone()
                is None
            )

    with connect(server_dsn) as connection:
        existing_databases = {
            row[0]
            for row in connection.execute(
                "SELECT datname FROM pg_database WHERE datname IN (%s, %s, %s)",
                (source_database, template_name, clone_name),
            ).fetchall()
        }
    assert existing_databases == {source_database}
