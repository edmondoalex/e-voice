# Ekonex Voice administration console

The server-rendered console uses the existing portal login, server-side session cookie,
tenant membership selection and CSRF controls. It does not introduce a parallel auth
system. Open `/dashboard` after logging in at `/login`.

## Views and authorization

- `/dashboard` lists only installations belonging to the selected tenant and derives
  online state from the EVCP liveness window.
- `/installations/{id}` shows searchable, paginated exposed entities with the effective voice
  name as the primary label, local icons, synchronized e-Control metadata, current state and
  availability. It distinguishes the latest complete Alexa Discovery snapshot from the estimated
  current inventory derived from active proactive deliveries, and shows redacted outcomes for the
  latest AddOrUpdateReport/DeleteReport. Tombstoned entities remain visible but cannot be
  controlled.
- `/installations/{id}/entities/{entity_id}/edit` edits cloud-only display and voice names
  for an entity belonging to the selected tenant and installation.
- `/activity` combines user/audit and connector lifecycle events. Filters remain bound
  to the selected tenant; foreign installation and entity identifiers return 404.
- `/system` reports tenant object counts, retained samples, the authoritative PostgreSQL
  database size in MB, retention policy, and last/next maintenance execution.

Owner, dealer administrator, installer and customer administrator roles may use the
console and commands. Read-only and customer-user memberships cannot. Direct controls
reuse the closed M6 command vocabulary and dispatcher; arbitrary Home Assistant service
calls are impossible. A result is displayed as successful only after the Connector
returns that outcome. Pending and final outcomes are audited with the authenticated user.

Lights expose direct ON/OFF controls and a 0–100% level slider. The portal validates the
percentage and maps it to the existing typed 0–255 M6 brightness field before dispatch. Controls
are disabled for unavailable or removed entities. Other supported domains expose only their
allowlisted value-free operations; no generic service or operation selector is rendered.

The effective voice name is the primary entity title and is also explicitly labelled. State
indicators distinguish on (green), off (grey), unavailable (red) and removed (orange), with the
corresponding ON/OFF control marked active. The light slider displays its selected percentage and
does not dispatch while dragging.

Command forms use progressive enhancement: JavaScript submits the existing CSRF-protected form
to the same tenant-scoped endpoint with `Accept: application/json`, renders bounded success/error
feedback inline and updates ON/OFF presentation after a confirmed success. Without JavaScript the
same form posts normally and returns the existing server-rendered outcome page. Authentication,
role checks, command validation, audit and Connector dispatch are shared by both paths.

The optional Connector-provided icon uses an allowlisted local SVG path. Unknown icons fall back
to a domain icon and then to a generic automation icon, so no remote asset or untrusted SVG is
rendered. Apply Alembic migration `20260820_0010` before deploying icon persistence.

## Entity names and voice aliases

The Connector-owned `friendly_name` is shown as **Nome e-Control** and remains read-only in
the console. Every inventory synchronization may update it without changing these optional
cloud-owned fields:

- **Nome visualizzato** (`display_name`): dashboard label; falls back to Nome e-Control;
- **Nome vocale** (`voice_name`): primary Alexa/voice label; falls back to Nome visualizzato,
  then Nome e-Control;
- **Alias vocali** (`voice_aliases`): up to 20 additional names, normalized and deduplicated
  case-insensitively.

Names are trimmed and limited to 120 characters. The form is HTML-escaped, CSRF-protected,
role-protected and installation/tenant-scoped. Saving a voice name or alias that collides
with another active entity in the same installation returns a conflict instead of choosing
an entity arbitrarily. Alexa discovery also fails closed for pre-existing ambiguous data by
omitting every endpoint involved in the collision. Alexa Smart Home discovery accepts one
primary `friendlyName`; aliases are retained by the centralized voice-name resolver and
participate in collision detection without changing the Alexa or EVCP protocol.

Updates and resets create `entity_names.updated` or `entity_names.reset` audit events. Audit
payloads contain only the entity identifier and changed field names, not the configured
names or aliases. Apply Alembic migration `20260819_0008` before deploying this feature.

## State history and storage policy

`entity_state_history` stores only changes to `(state, available)`, not repeated samples.
Domains in `EKONEX_STATE_HISTORY_EXCLUDED_DOMAINS` are excluded; the production-oriented
default is `sensor` because high-frequency sensor history is not needed for direct device
control. This can be adjusted explicitly per deployment.

Defaults:

- state history: 30 days;
- connector operational events: 30 days;
- administrative audit: 365 days;
- portal login attempts: 30 days;
- expired portal sessions: removed after their own expiry.

Docker Compose starts the isolated `maintenance` service automatically. It executes once
per day at 03:00 UTC by default, catches up a missed daily boundary after a restart, and
does not share process lifecycle with the API, PostgreSQL, Redis or Connector. Override
the hour with `EKONEX_CLEANUP_SCHEDULE_HOUR_UTC`.

The same idempotent operation remains available for an explicit on-demand run:

```shell
python -m apps.cloud_api.app.cleanup
```

Each run records its start/completion time, status, duration and deleted row counts in
`maintenance_runs`. Logs contain the same bounded summary but never credentials, tokens,
payloads or exception messages. Scheduler/database errors are retried without stopping
other services.
Retention periods are configured with the corresponding `EKONEX_*_RETENTION_DAYS`
variables in `.env.example`. Apply Alembic migration `20260819_0007` before deployment.

## Audit coverage

The existing `audit_events` table remains the authoritative user/security audit instead
of creating a duplicate schema. Login, logout, tenant selection, pairing claims and admin
commands are recorded with tenant and user context. `operational_events` separately records
bounded Connector connect/disconnect lifecycle events. EVCP heartbeat traffic is not stored.
