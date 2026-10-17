# PlanGuard

PlanGuard is a pre-merge safety gate for PostgreSQL schema migrations. It is
intended to combine SQL analysis, measured migration behavior, and query-plan
checks in one report.

The project includes a PostgreSQL runtime and synthetic seeding, the
`planguard analyze` SQL migration analyzer (rules PG001–PG014), and isolated
migration execution in temporary experiment databases. Podman is preferred,
with Docker as a fallback, or a caller-supplied DSN can bootstrap experiments.
Migration SQL should not run directly against the database named by that DSN.
Workload-driven performance measurement and before/after query-plan comparison
are not implemented yet. See [STATUS.md](STATUS.md) for phase details and
verification evidence.

## Development setup

Use the project virtual environment so dependencies stay isolated:

```sh
python3.12 -m venv .venv
make install
make test
make lint
make format-check
make typecheck
make test-integration
```
