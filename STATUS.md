# Build status

PlanGuard has completed Phases 0–3. The project has a PostgreSQL runtime and
seeding harness, a SQL migration analyzer, and an isolated migration executor.
Live PostgreSQL integration tests are passing using the configured container
runtime.

## Phase 0 — Project scaffold

- Python 3.12.15 environment and editable package installation verified.
- `pglast==7.10` installed and dependency checks passed.
- Initial unit tests, lint, formatting, and strict type checking passed.

## Phase 1 — PostgreSQL runtime and seeding

- Container runtime selection prefers Podman and falls back to Docker.
- A caller-supplied DSN or `PLANGUARD_DSN` can provide the PostgreSQL server
  connection used to bootstrap experiments.
- Experiments use generated temporary databases, optionally cloned from a
  template; cleanup is limited to databases created by the current run.
- Schema inspection supports synthetic data profiles for common PostgreSQL
  types and discovered enum values.
- Unsupported non-nullable types are rejected rather than guessed.

## Phase 2 — Migration analyzer

- The `planguard analyze` CLI parses SQL migrations using `pglast`.
- Analyzer rules PG001–PG014 report migration risks with severity and rationale.
- A smoke test confirmed that the CLI reports warnings for a regular index and
  a volatile column default, as well as a missing positive `lock_timeout`.
- Findings are based on parsed SQL rather than simple text matching.

## Phase 3 — Isolated migration execution

- Migration SQL is executed against a temporary experiment database, not
  directly against the supplied bootstrap server database.
- Execution results report migration name, success or failure, elapsed time,
  and available database error details.
- Temporary experiment databases are cleaned up on both successful and failed
  migration execution.
- Unit tests cover successful execution, SQL errors, and timeout validation.
- Integration tests verify execution and database cleanup against PostgreSQL.

## Latest verification

The following checks passed after Phase 3 implementation:

- `make test`: **66 passed, 3 deselected**.
- `make test-integration`: **3 passed, 66 deselected**.
- `make lint`: **All checks passed**.
- `make format-check`: **29 files already formatted**.
- `make typecheck`: **Success: no issues found in 21 source files**.

The Phase 3 implementation was committed and pushed as
`d56a577` (`Implement isolated migration execution`).

## Not yet implemented

- Workload-driven performance measurement, including write latency and lock
  waits.
- Before/after query-plan comparison.
- Reporting features beyond the current analyzer findings and migration
  execution result.
- Automated migration rewrites, scaling features, external integrations, and
  benchmark scenarios.

No benchmark results have been established yet.
