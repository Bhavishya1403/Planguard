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
- **Status:** Recorded as a design choice only; runtime lifecycle code has not been implemented or verified.

## Declare a console command only when its module exists

- **Decision:** Keep the package entry point out of phase 0 metadata until `planguard.cli` is implemented.
- **Alternatives considered:** Install a `planguard` command that points at a missing module; add a placeholder CLI before the analyzer phase.
- **Why rejected:** A broken or misleading executable would make a successful package install appear to provide a CLI that does not work. The analyzer command belongs to a later build phase.
- **Trade-off:** Phase 0 verifies editable package installation and shared models, but it cannot provide `planguard analyze` yet.
