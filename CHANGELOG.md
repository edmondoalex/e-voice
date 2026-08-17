# Ekonex Voice change log

This file tracks completed project milestones and repository-level changes.
Add new entries in reverse chronological order and include scope, validation,
commit references, and deviations from `docs/SPEC_V1.md`.

## 2026-08-17 — M2 secure pairing

Status: Ready for review

### Scope

- Added short-lived, one-time pairing sessions with `ABCD-1234` style codes.
- Separated the human code, connector polling secret, and durable Connector
  credential into independent security values.
- Added HMAC/SHA-256 hashing, encrypted one-time credential delivery, expiry,
  replay protection, and persistent per-user brute-force limits.
- Added tenant-safe claim, installation creation, credential revocation and
  rotation, and redacted audit events.
- Added an Alembic migration for pairing sessions, claim attempts, and Connector
  credentials.
- Added tests for expiry, replay, brute force, cross-tenant attempts, one-time
  delivery, polling authorization, audit redaction, revocation, and rotation.

### Validation

- `ruff format --check .`: passed
- `ruff check .`: passed
- `mypy apps`: passed
- `pytest`: passed
- Alembic upgrade and model metadata parity: passed
- PostgreSQL migration SQL generation: passed
- Secret pattern scan: no findings

### Commit

- `M2: secure installation pairing`

### Deviations

- Pairing is implemented as a transport-independent core service. HTTP routes
  and the HAOS config flow remain outside M2 because authenticated portal
  transport and the Home Assistant Connector belong to later milestones.
- No Alexa, Amazon OAuth, entity synchronization, or M3 behavior was added.

## 2026-08-17 — M1 core multi-tenant backend

Status: Complete

### Scope

- Added SQLAlchemy models for dealers, users, tenants, memberships,
  installations, entities, Alexa publications, and audit events.
- Added the initial Alembic schema migration with UUID keys, constraints,
  foreign-key deletion behavior, and required lookup indexes.
- Added active membership resolution as the basic authentication scaffold.
- Added centralized tenant contexts and role-based publication write policy.
- Added repository and service methods that scope tenant-owned queries in SQL,
  including indirect entity and publication ownership.
- Added unit, authorization, migration parity, and explicit cross-tenant tests.
- Added `aiosqlite` as a development-only dependency for isolated database tests.

### Validation

- `ruff format --check .`: passed
- `ruff check .`: passed
- `mypy apps`: passed
- `pytest`: passed
- Alembic upgrade and model metadata parity: passed
- PostgreSQL migration SQL generation: passed

### Commit

- `M1: core multi-tenant backend`

### Deviations

- Tests use in-memory SQLite for speed and isolation; production remains
  PostgreSQL through the configured async database URL.
- Basic authentication is intentionally limited to resolving active users and
  tenant memberships. Login, token issuance, and OAuth are outside M1.
- No M2 pairing behavior was implemented.

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
