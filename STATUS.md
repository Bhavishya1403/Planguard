# Build status

Phase 0 remains verified. Phase 1 runtime and seeding code is implemented, but
the phase is **not fully verified** because the PostgreSQL integration test
could not run against a live server in this environment.

## Phase 0 evidence

- The editable install and pinned `pglast==7.10` installation succeeded in
  `.venv` using Python 3.12.15.
- `./.venv/bin/python -m pip check` reported no broken requirements.
- Phase 0 checks passed: 11 unit tests, Ruff lint, Ruff formatting, and strict
  mypy across the original 12 source files.

## Phase 1 implementation

- PostgreSQL containers are launched and cleaned up through bounded subprocess
  calls. Automatic runtime selection prefers Podman, with Docker as the
  fallback; callers can explicitly select either runtime.
- Server selection accepts a caller-supplied `--dsn` value before
  `PLANGUARD_DSN`. The supplied DSN is a bootstrap connection. Each experiment
  uses a generated temporary database, optionally cloned from a template.
- Database identifiers are validated and quoted. Catalog filters and inserted
  values are parameterized. Temporary cleanup runs only after that object
  successfully creates its database.
- Schema inspection reads defaults, identity/generated columns, enum values,
  character lengths, and numeric bounds. Synthetic profiles cover common
  PostgreSQL types, use discovered enum labels, and refuse unsupported
  non-nullable types rather than guessing.
- Database-free unit coverage exercises runtime selection and lifecycle calls,
  DSN precedence, identifier and ownership safety, and synthetic profile
  generation. PostgreSQL integration coverage creates, seeds, clones, and
  removes isolated databases.
- No analyzer rules, workload measurement, or later-phase features were added.
  The package still has no console entry point; the existing design decision is
  to add it when a working `planguard.cli` module is introduced.

## Phase 1 verification evidence

- `make test`: **47 passed, 1 deselected** (the deselected test is marked
  integration).
- `make lint`: **All checks passed**.
- `make format-check`: **20 files already formatted**.
- `make typecheck`: **Success: no issues found in 15 source files**.
- `make test-integration`: **1 skipped, 47 deselected**. No integration test
  ran against PostgreSQL. The Podman executable is present, but `podman run`
  fails before container creation because its setup cannot set the sticky bit
  on `/run/user/1000/libpod` (`read-only file system`). Docker is unavailable,
  and neither `PLANGUARD_TEST_DSN` nor `PLANGUARD_DSN` is configured.
- The schema-introspection SQL was also accepted by the installed `pglast`
  parser. This validates syntax only; it does not replace PostgreSQL execution.

## Not verified or not implemented

- Live PostgreSQL integration remains unverified. No integration test has
  passed against PostgreSQL.
- No analyzer rules, workload measurement, plan diffing, reports, rewrites,
  scaling, integrations, or benchmark scenarios have been implemented.
- No benchmark results are available.
