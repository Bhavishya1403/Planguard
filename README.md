# PlanGuard

PlanGuard is a pre-merge safety gate for PostgreSQL schema migrations. It is
intended to combine SQL analysis, measured migration behavior, and query-plan
checks in one report.

The repository includes the Phase 0 package and model foundation plus Phase 1
PostgreSQL runtime, isolated experiment database, and synthetic seeding APIs. A
caller can pass the value from `--dsn`; when omitted, the server selector reads
`PLANGUARD_DSN` and otherwise starts Podman (preferred) or Docker. Experiments
use a temporary database created for that run, not the database named by the
external DSN. There is not yet a console command, analyzer, workload runner, or
benchmark result. See [STATUS.md](STATUS.md) for verification evidence and the
integration-test infrastructure blocker.

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
