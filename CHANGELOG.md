# Ekonex Voice change log

This file tracks completed project milestones and repository-level changes.
Add new entries in reverse chronological order and include scope, validation,
commit references, and deviations from `docs/SPEC_V1.md`.

## 2026-08-17 — Ekonex Home Assistant Integration Standard

Status: Documented; implementation not started

### Scope

- Declared HAOS/Home Assistant OS (Hassio) as the primary integration platform.
- Added the Ekonex Home Assistant Integration Standard to `docs/SPEC_V1.md`.
- Added a mandatory pre-M3 review of previous Ekonex Home Assistant components.
- Required alignment across lifecycle, config flow, diagnostics, reconnect,
  logging, naming, `unique_id`, translations, and error handling.
- Added the completed compatibility analysis as an M3 entry criterion.
- Explicitly kept M1 and all implementation work outside this documentation
  change.

### Validation

- Documentation diff and Markdown whitespace check: passed
- Application code and dependencies: unchanged

### Commit

- `docs: define Ekonex Home Assistant integration standard`

### Deviations

- No deviation from the requested documentation-only scope.

## 2026-08-17 — M0 repository bootstrap

Status: Complete

### Scope

- Initialized the Ekonex Voice monorepo on `main`.
- Added the Python 3.13 FastAPI cloud API bootstrap.
- Added environment-backed Pydantic settings.
- Added async SQLAlchemy and Alembic infrastructure.
- Added PostgreSQL and Redis development services with Docker Compose.
- Added the `GET /health` liveness endpoint.
- Added pytest, pytest-asyncio, Ruff, mypy, and GitHub Actions checks.
- Added the root documentation, canonical specification, and ADRs 0001–0006.
- Kept Alexa, OAuth, pairing, Home Assistant, WebSocket, UI, and domain models
  outside the milestone.

### Validation

- `ruff format --check .`: passed
- `ruff check .`: passed
- `mypy apps`: passed
- `pytest`: 2 passed
- Alembic offline SQL generation: passed
- Docker Compose YAML and service graph validation: passed
- Secret pattern scan: no findings

### Commits

- `0daf77a` — `M0: repository bootstrap`

### Deviations

- Native `docker compose config` and container startup were not executed because
  Docker CLI was unavailable on the development machine. Compose was validated
  through YAML parsing and structural assertions.
- No architectural deviations from `docs/SPEC_V1.md`.
