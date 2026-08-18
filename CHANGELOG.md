# Ekonex Voice change log

This file tracks completed project milestones and repository-level changes.
Add new entries in reverse chronological order and include scope, validation,
commit references, and deviations from `docs/SPEC_V1.md`.

## 2026-08-18 — M3 Home Assistant Connector foundation

Status: Ready for review

### Scope

- Added the HAOS-first native `ekonex_voice` manifest, ConfigEntry lifecycle and
  typed runtime data.
- Added an async, secret-safe HTTP boundary for M2 pairing and final credential
  validation.
- Added UI pairing, stable installation identity, duplicate prevention and
  ConfigEntry-linked reauthentication.
- Added a single cancellation-safe connection supervisor with capped full-jitter
  backoff, ready for later EVCP WebSocket transport.
- Added recursively redacted diagnostics and matching English/Italian runtime
  translation catalogs.
- Added HA config-flow, lifecycle, reconnect, security, diagnostics, metadata and
  localization tests plus an exact HAOS acceptance procedure.

### Validation

- Backend pytest on Windows: 23 passed
- Full Linux pytest with Home Assistant harness: 44 passed
- `ruff format --check .`: passed
- `ruff check .`: passed
- `mypy apps custom_components`: passed
- `docker compose config --quiet`: passed in CI
- `git diff --check`: passed

### Scope guard

- No Alexa, entity synchronization, EVCP WebSocket wire implementation, command
  mapper, portal UI or production infrastructure was added.

### Deviations

- `strings.json` is intentionally absent. Current official Home Assistant custom
  integration documentation (reviewed 2026-08-18) requires full runtime text in
  `translations/*.json` and says not to use Core's build-time `strings.json`
  pipeline. This current rule takes precedence over the older M3 requirement.
- A real HAOS acceptance run was not possible on the Windows development host;
  `docs/home-assistant.md` records the exact manual procedure. Linux CI executes
  the complete automated Home Assistant suite.

## 2026-08-17 — Pre-M3 Home Assistant standard

Status: Ready for review

### Scope

- Inventoried eight accessible Ekonex Home Assistant components at pinned
  revisions and recorded their applicable technical evidence.
- Defined the Ekonex Home Assistant Integration Standard for HAOS, ConfigEntry
  lifecycle, config flows, identity, registry, diagnostics, reconnect, logging,
  localization, errors, updates and recovery.
- Classified previous conventions as reusable, requiring normalization, or to be
  discarded where current Home Assistant practices take precedence.
- Defined the minimum automated and HAOS acceptance test gate for the future M3
  Connector without creating or implementing it.

### Validation

- `pytest`: 23 passed
- `ruff format --check .`: passed
- `ruff check .`: passed
- `mypy apps`: passed
- `git diff --check`: passed

### Commit

- `docs: complete pre-M3 Home Assistant standard`

### ADR assessment

- No ADR was added: the analysis applies existing specification and ADR
  decisions and introduces no new architectural departure.

### Deviations

- None. M3, entity synchronization, WebSocket implementation and Alexa remain
  out of scope.

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
- Made successful claims atomic and added a transaction-durability regression
  test covering the installation, credential, pairing state, and audit event.

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
