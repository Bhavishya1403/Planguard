# Design decisions

## Stop when `pglast` cannot be installed

- **Decision:** Do not implement or ship SQL parsing until the required `pglast` dependency can be installed and exercised.
- **Alternatives considered:** Regex-based parsing; an unrequested SQL parser with different PostgreSQL grammar coverage; continuing to build components that depend on an unverified parser.
- **Why rejected:** The build requirements explicitly require a real PostgreSQL parser and direct the build to stop if `pglast` cannot be installed. Regex matching can misclassify SQL and would undermine the static analyzer's core safety claim. The package index was unreachable in this environment, so the failed lookup does not prove that the pinned version is unavailable in general.
- **Trade-off:** The initial build paused before implementing SQL parsing. The environment-specific dependency blocker is now cleared; phase 0 installation has been verified, and this decision remains a guardrail for future environments.

## Keep infrastructure behind a runtime boundary

- **Decision:** The planned PostgreSQL runtime integration will call Podman or Docker through subprocesses, with an external DSN path, as specified in the build prompt.
- **Alternatives considered:** A Docker SDK or a container-only execution model.
- **Why rejected:** A CLI boundary supports the owner's Podman environment and avoids a second runtime dependency; external-server mode supports hosts without a local container runtime.
- **Status:** Implemented in Phase 1. Unit coverage verifies runtime selection and bounded lifecycle calls; live PostgreSQL startup could not be verified in this sandbox.

## Isolate experiments on external PostgreSQL servers

- **Decision:** Treat an external DSN as a bootstrap connection. Create a unique experiment database for each run, optionally cloned from a template, and apply experiment schema changes only to that database.
- **Alternatives considered:** Run migrations in the database named by the supplied DSN; accept an arbitrary database name for cleanup.
- **Why rejected:** The supplied database may contain user data and is not owned by PlanGuard. A unique database created by the current run gives the experiment an explicit cleanup boundary.
- **Safety details:** Database identifiers are validated and quoted with Psycopg's SQL composition helpers; catalog values are parameterized. Cleanup is guarded by the temporary database object's successful-creation state and terminates sessions only in that owned database.
- **Status:** Implemented in Phase 1. PostgreSQL integration behavior remains unverified in environments without a usable server or container runtime.

## Generate fallback rows from inspected column types

- **Decision:** Inspect table columns and generate deterministic values for supported PostgreSQL types, using discovered enum values and server defaults where available. Refuse unsupported non-nullable types instead of inventing values likely to fail inserts.
- **Alternatives considered:** Hard-code one schema's data shape; guess string values for every unknown type.
- **Why rejected:** Phase 1 seeding should support different schemas without hiding incompatibilities behind guesses.
- **Status:** Implemented in Phase 1; integration verification requires PostgreSQL.

## Declare a console command only when its module exists

- **Decision:** Keep the package entry point out of package metadata until `planguard.cli` is implemented.
- **Alternatives considered:** Install a `planguard` command that points at a missing module; add a placeholder CLI before the analyzer phase.
- **Why rejected:** A broken or misleading executable would make a successful package install appear to provide a CLI that does not work. The analyzer command belongs to a later build phase.
- **Trade-off:** The current package provides reusable Phase 1 runtime and database APIs, but no `planguard` command or `planguard analyze` workflow yet.
