# Ekonex Voice administration console

The server-rendered console uses the existing portal login, server-side session cookie,
tenant membership selection and CSRF controls. It does not introduce a parallel auth
system. Open `/dashboard` after logging in at `/login`.

## Views and authorization

- `/dashboard` lists only installations belonging to the selected tenant and derives
  online state from the EVCP liveness window.
- `/installations/{id}` shows searchable, paginated exposed entities and their current
  availability. Tombstoned entities remain visible but cannot be controlled.
- `/activity` combines user/audit and connector lifecycle events. Filters remain bound
  to the selected tenant; foreign installation and entity identifiers return 404.
- `/system` reports tenant object counts, retained samples, the authoritative PostgreSQL
  database size in MB, retention policy, and last/next maintenance execution.

Owner, dealer administrator, installer and customer administrator roles may use the
console and commands. Read-only and customer-user memberships cannot. Direct controls
reuse the closed M6 command vocabulary and dispatcher; arbitrary Home Assistant service
calls are impossible. A result is displayed as successful only after the Connector
returns that outcome. Pending and final outcomes are audited with the authenticated user.

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
