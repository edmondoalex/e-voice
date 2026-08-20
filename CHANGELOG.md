# Ekonex Voice change log

This file tracks completed project milestones and repository-level changes.
Add new entries in reverse chronological order and include scope, validation,
commit references, and deviations from `docs/SPEC_V1.md`.

## 2026-08-20 — Latest Alexa activity summary

Status: Ready for review

### Scope

- Added a concise portal summary derived from the newest complete Discovery,
  AddOrUpdateReport or DeleteReport timestamp and outcome.
- Preserved the separate complete snapshot, proactive report and estimated current inventory
  sections, including the original meaning of `Ultima Discovery`.

### Scope guard

- No query, persistence, Alexa protocol, proactive sync, Account Linking, Lambda, EVCP,
  Connector or endpoint identity changes.

## 2026-08-20 — AJAX entity command form encoding fix

Status: Ready for review

### Scope

- Encoded progressive-enhancement command submissions as URL-encoded form data, matching the
  working non-JavaScript form and the intentionally bounded portal form parser.
- Added regression coverage for the rejected multipart payload and successful ON, OFF and
  SET LIGHT LEVEL dispatch through the unchanged secure command path.

### Scope guard

- No dispatcher, authorization, CSRF, tenant, Alexa, EVCP, database or Connector changes.

## 2026-08-20 — Entity control UX polish

Status: Ready for review

### Scope

- Made the effective voice name both the primary entity title and an explicit labelled value.
- Added distinct on, off, unavailable and removed state indicators plus active ON/OFF controls.
- Added progressively enhanced inline command submission and feedback while retaining the
  server-rendered non-JavaScript result page.
- Displayed the current light level percentage beside the explicit 0–100% slider.

### Scope guard

- No Alexa, EVCP, database, entity identity, authorization or dispatcher contract changes.

## 2026-08-20 — Entity controls, icons and Alexa inventory visibility

Status: Ready for review

### Scope

- Redesigned installation entity rows around the effective voice name, synchronized icon,
  e-Control metadata, availability and compact direct controls.
- Added typed ON/OFF and 0–100% light-level controls through the existing safe M6 dispatcher;
  removed/unavailable entities cannot be operated.
- Extended EVCP entity inventory compatibly with an optional icon and persisted it with migration
  `20260820_0010`, using only local allowlisted SVG paths with domain/unknown fallbacks.
- Distinguished the last complete Alexa Discovery snapshot from the estimated current Alexa
  inventory derived from the existing proactive-delivery ledger.

### Scope guard

- No arbitrary service passthrough, Alexa endpoint/fingerprint change, parallel inventory,
  authentication change or entity identity change.

## 2026-08-20 — Alexa Lambda AcceptGrant forwarding

Status: Ready for review

### Scope

- Allowed only `Alexa.Authorization/AcceptGrant` through the Lambda authorization precheck without
  a regular directive scope, preserving its Amazon grant and grantee payload for the cloud.
- Kept mandatory BearerToken validation unchanged for Discovery, control and all other directives.
- Added a production-payload regression test and Lambda deployment documentation.

## 2026-08-20 — Alexa Discovery observability and proactive synchronization

Status: Ready for review

### Scope

- Added tenant/installation-scoped snapshots of the latest Alexa Discovery endpoint metadata and
  new/renamed/removed diff, with no directive credentials persisted.
- Added proactive AddOrUpdateReport/DeleteReport delivery through the existing encrypted
  AcceptGrant/LWA/Event Gateway implementation and the existing Alexa endpoint mapper.
- Added per-account delivery fingerprints for idempotency, bounded retry, redacted audit outcomes,
  portal visibility, migration `20260820_0009` and focused isolation/security coverage.

### Scope guard

- No parallel entity inventory, Lambda protocol change, EVCP change or credential exposure.

## 2026-08-20 — Alexa Lambda cloud discovery adapter

Status: Ready for review

### Scope

- Added a deployable dependency-free AWS Lambda handler that forwards Alexa Smart Home v3
  directives to the existing tenant-scoped Ekonex Cloud adapter.
- Added bounded HTTPS transport, response validation, secret-free diagnostics and Alexa-native
  invalid/expired authorization, rate-limit and backend failure responses.
- Distinguished expired from invalid/revoked cloud access tokens and added focused discovery
  and authentication tests plus exact deployment instructions for `ekonex-voice`.

### Scope guard

- No parallel entity/device model, EVCP change, Connector change or new cloud inventory API.

## 2026-08-19 — Entity custom display and voice names

Status: Ready for review

### Scope

- Added persistent, tenant-scoped display-name, voice-name and voice-alias overrides while
  retaining the Connector-synchronized e-Control name as immutable source metadata.
- Added centralized fallback, normalization, deduplication and collision handling shared
  by the administration console and Alexa discovery.
- Added an authenticated CSRF-protected editor, reset action and redacted audit events.
- Added migration `20260819_0008` and regression coverage for synchronization, isolation,
  dashboard behavior, collisions and Alexa discovery.

### Scope guard

- No EVCP, Connector, entity identity, pairing or authentication contract changes.

## 2026-08-19 — Home Assistant visible-name synchronization 0.1.6

Status: Ready for review

### Scope

- Gave the current Home Assistant state `friendly_name` absolute precedence when
  serializing entity inventory metadata, with registry names retained as fallbacks.
- Added a regression covering a UI rename when the entity registry still exposes the
  preceding configured name, without changing entity or registry identity.

### Scope guard

- No EVCP schema, cloud persistence, pairing, authentication or entity identity changes.

## 2026-08-19 — Automatic database maintenance

Status: Ready for review

### Scope

- Added an isolated daily Docker maintenance service that reuses the idempotent retention
  cleanup and catches up missed scheduled runs after restart.
- Persisted secret-free execution status, duration and per-category deletion counts.
- Extended `/system` with real PostgreSQL size in MB, maintenance status/schedule and the
  configured retention policy.
- Added migration `20260819_0007`, scheduler/failure tests and deployment documentation.

### Scope guard

- No API, EVCP, Connector, entity synchronization or command behavior changes. A scheduler
  failure cannot stop or restart application services.

## 2026-08-19 — Administration console navigation

Status: Ready for review

### Scope

- Replaced the `/installations` redirect with a tenant-scoped server-rendered plant list
  including connectivity, versions, exposed entity count and last contact.
- Added accessible active-menu state and verified every console navigation target uses a
  direct server-rendered link without JavaScript.
- Added navigation, status and tenant-isolation regression tests.

### Scope guard

- No authentication, API, EVCP, persistence schema, Connector or command behavior changes.

## 2026-08-19 — Portal branding pass

Status: Ready for review

### Scope

- Replaced the 64×64 portal image with the supplied high-resolution Ekonex Cloud Voice
  asset while preserving the small icon for technical/HACS use.
- Unified login, pairing, tenant selection and administration console branding.
- Updated presentation-only Home Assistant wording to e-Control in portal HTML and added
  the compact console logo/navigation treatment.

### Scope guard

- No authentication, pairing behavior, API, EVCP, database, Connector or internal naming
  changes.

## 2026-08-19 — Multi-installation administration console

Status: Ready for review

### Scope

- Added a tenant-isolated, server-rendered dashboard, installation/entity view, activity
  view and system/storage statistics using the existing portal session authentication.
- Added safe direct controls through the closed M6 command dispatcher with CSRF,
  role checks, tenant-scoped target lookup and authenticated pending/final audit events.
- Added change-only entity state history, bounded Connector lifecycle events, configurable
  retention and an idempotent scheduler-friendly cleanup command.
- Added Alembic migration `20260819_0006`, tests and operations documentation.

### Scope guard

- No console-specific Home Assistant custom component, pairing protocol, EVCP wire format,
  Alexa or LICENSE changes. Package version is `0.2.0`; the merged HACS integration fix
  remains at `0.1.5`.

## 2026-08-19 — Home Assistant effective entity-name synchronization

Status: Ready for review

### Scope

- Corrected inventory metadata naming so an explicit entity user override wins,
  otherwise the current state-machine `friendly_name` visible in Home Assistant
  is preferred over stale registry/original metadata.
- Preserved fallbacks for current and future registry APIs (`name_by_user`,
  `name`, `original_name`) without changing stable entity or registry identity.
- Added a production-shaped regression test proving that renaming
  `BusPro Luci Luce Ufficio Alex` to `Luce Ufficio Alex` automatically emits a
  new `inventory_full` with the new name.
- Corrected the all-registry subscriptions: keyed Home Assistant helpers require
  explicit IDs and treated `MATCH_ALL` as a literal key, so entity/device
  metadata changes previously never reached the resync callback.
- Bumped the custom integration version to `0.1.5`.

### Scope guard

- No pairing, cloud API, EVCP, Alexa, device, area, state or tombstone changes.

## 2026-08-19 — Home Assistant options reload compatibility

Status: Ready for review

### Scope

- Removed the redundant ConfigEntry update listener that Home Assistant 2026.6
  rejects when an integration uses `OptionsFlowWithReload`.
- Kept native options-flow reload semantics so saved device, entity and label
  selections are read by a fresh synchronizer and immediately produce a new
  `inventory_full` snapshot.
- Added a regression test that loads the integration, verifies it registers no
  update listener, saves an entity selection and observes exactly one reload.
- Bumped the custom integration version to `0.1.4`.

### Scope guard

- No pairing, cloud API, EVCP, Alexa or authentication changes.

## 2026-08-19 — Production entity-sync diagnostics

Status: Ready for review

### Scope

- Confirmed and documented that exposure remains strictly opt-in and an empty
  initial inventory is an explicit valid reconciliation.
- Added secret-safe inventory lifecycle logging and bounded HA diagnostics for
  snapshot revision/count, state updates and send failures.
- Added transport error recovery so a WebSocket send failure enters the existing
  bounded reconnect path instead of terminating the supervisor silently.
- Added HA/EVCP/cloud persistence contract tests for initial/empty snapshots,
  reconnect full resync, state/unavailable updates, removal and installation
  isolation.
- Bumped the custom integration version to `0.1.3` for HAOS deployment.

### Scope guard

- No changes to pairing, portal authentication, OAuth, Alexa or command mapping.

## 2026-08-19 — Pairing portal completion

Status: Ready for review

### Scope

- Added a tenant-authorized pairing claim endpoint backed by the existing
  `PairingService.claim()` flow.
- Added the mobile-friendly Italian `/pair` portal with signed CSRF protection,
  safe error mapping and no credential disclosure.
- Added Argon2 password login, opaque revocable server-side sessions, verified
  membership-based tenant selection, login throttling and logout.
- Added an interactive one-time bootstrap command with no default credential and
  an Alembic migration for portal sessions and login-attempt metadata.
- Enforced existing write roles for pairing claims and retained one-shot
  Connector credential delivery exclusively through Home Assistant polling.
- Updated Home Assistant pairing help with the production portal URL and bumped
  the HACS integration version to `0.1.2`.

### Authentication boundary

- The portal now works directly behind the existing Cloudflare/Caddy deployment;
  it does not depend on identity headers injected by an authenticating ingress.
- Tenant authorization still uses the existing `AuthenticationService` and
  `TenantMembership` roles. A future administration UI remains out of scope.
- Removed the official HACS catalog validator workflow: this proprietary
  integration is distributed only as a HACS Custom Repository; Hassfest remains.

### Scope guard

- No changes to LICENSE, Alexa, EVCP, entity sync or command execution.

## 2026-08-18 — M7 Alexa Smart Home

Status: Ready for review

### Scope

- Added an Alexa Smart Home v3 cloud adapter with tenant-scoped discovery,
  state reports, typed M6 command routing and bounded account-scoped replay.
- Added OAuth 2.0 authorization-code account linking, optional PKCE S256,
  one-use grants, short access tokens, refresh rotation and revocation.
- Added safe mappings for light, switch, cover, climate, fan and scene, while
  excluding unsupported and security-sensitive Home Assistant domains.
- Added persistence migration, automated tests and Amazon console/acceptance
  documentation.
- Added encrypted Alexa Event Gateway authorization plus M5-driven, deduplicated
  ChangeReport delivery with token refresh and bounded retry.
- Corrected blinds discovery/control to use Amazon's current RangeController or
  ModeController semantics consistently for open, close and position.

### Scope guard

- No conversational skill, lock/alarm/camera control, billing, portal work or
  later milestone functionality.

## 2026-08-18 — M6 cloud-to-Home Assistant command execution

Status: Ready for review

### Scope

- Added typed EVCP command/results with session binding, bounded timeout,
  correlation and replay protection.
- Added installation-scoped cloud dispatch, active-inventory authorization and
  redacted command audits.
- Added fixed HA mappers for light, switch, cover, climate, fan, scene, script,
  button, number and select with capability/range validation.
- Kept Home Assistant state authoritative through M5 synchronization.

### Scope guard

- No arbitrary HA service passthrough, Alexa, OAuth, admin UI or later milestone.

### Architecture

- No new ADR: this implements ADR-0004, ADR-0005 and ADR-0007.

## 2026-08-18 — M5 entity inventory and state synchronization

Status: Ready for review

### Scope

- Added strictly opt-in exposure through the Ekonex Voice options UI and a
  manually installer-created Home Assistant label stored by stable registry ID.
- Added union semantics for UI-selected devices/entities and label-selected
  devices/entities, with automatic reconciliation on registry changes.
- Added deterministic bounded EVCP inventory batches, monotonic revisions,
  coalesced state updates, reconnect full sync and strict attribute redaction.
- Preserved arbitrary installer-selected HA domains and current user-configured
  HA entity names while keeping stable registry identity across renames.
- Added installation-scoped cloud upsert, state authorization, synchronization
  metadata and tombstones for removed or deselected entities.
- Added database migration, tests, diagnostics counters and HAOS acceptance steps.

### Scope guard

- No command execution, Alexa/provider behavior, arbitrary services, admin UI or
  later milestone work was implemented.

### Specification alignment

- Issue #10's final installer-controlled policy supersedes SPEC_V1's earlier
  M5 `light`/`switch` publication filter: explicitly exposed HA domains are
  transported, while attributes remain deny-by-default and safely normalized.

## 2026-08-18 — M4 EVCP WebSocket transport and cloud connection

Status: Ready for review

### Scope

- Added the authenticated `/connector/v1/ws` backend and HA outbound WSS client.
- Added strict EVCP v1 hello/ack, heartbeat/liveness, message bounds, close codes,
  cancellation-safe cleanup and secret-safe credential binding.
- Added latest-session-wins ownership behind a generation-safe registry and
  recorded that durable choice in ADR-0007.
- Added protocol, credential revocation, vocabulary and session replacement tests.

### Scope guard

- No entity inventory/state sync, command execution, Alexa/provider behavior or
  milestone after M4 was implemented.

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
