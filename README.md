# PlanGuard

PlanGuard is a pre-merge safety gate for PostgreSQL schema migrations. It is
intended to combine SQL analysis, measured migration behavior, and query-plan
checks in one report.

The repository is currently at phase 0: package metadata, configuration models,
and shared result models. The analyzer and runtime pipeline are not implemented
yet. See [STATUS.md](STATUS.md) for verified and unverified work. No benchmark
results are available yet.

## Development setup

Use the project virtual environment so dependencies stay isolated:

```sh
python3.12 -m venv .venv
make install
make test
make lint
make format-check
make typecheck
```
