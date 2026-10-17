"""Command-line entry point for database-free PlanGuard analysis."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from pglast.parser import ParseError

from planguard.analyzer import PostgresVersion, analyze_migration

app = typer.Typer(
    no_args_is_help=True,
    help="Analyze PostgreSQL migrations before deployment.",
)


@app.callback()
def _root() -> None:
    """Group commands under the planguard executable."""


@app.command()
def analyze(
    migration: Annotated[
        Path,
        typer.Argument(
            ...,
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ],
    pg_version: Annotated[
        str,
        typer.Option(
            "--pg-version",
            help="Target PostgreSQL version.",
        ),
    ] = "16",
) -> None:
    """Analyze a PostgreSQL migration file without connecting to a database."""
    try:
        version = PostgresVersion.parse(pg_version)
        source = migration.read_text(encoding="utf-8")
        findings = analyze_migration(source, pg_version=version)
    except (OSError, UnicodeError, ValueError, ParseError) as error:
        typer.echo(f"planguard: {error}", err=True)
        raise typer.Exit(code=2) from error

    if not findings:
        typer.echo(f"No findings for PostgreSQL {pg_version}.")
        return

    for finding in findings:
        typer.echo(
            f"{migration}:{finding.line}: {finding.rule_id} {finding.severity} "
            f"[{finding.lock_mode}; blocks {finding.blocks}]"
        )
        typer.echo(f"  {finding.statement}")
        typer.echo(f"  {finding.explanation}")
        if finding.rewrite is not None:
            typer.echo(f"  Suggested migration: {finding.rewrite}")


def main() -> None:
    """Run the Typer application as the installed console script."""
    app()
