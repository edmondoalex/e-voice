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
- `/system` reports tenant object counts, retained samples and PostgreSQL database size.

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

Run cleanup from a scheduler (cron, systemd timer, Kubernetes CronJob or equivalent):

```shell
python -m apps.cloud_api.app.cleanup
```

The job is idempotent, prints counts only, and never logs credentials or payload secrets.
Retention periods are configured with the corresponding `EKONEX_*_RETENTION_DAYS`
variables in `.env.example`. Apply Alembic migration `20260819_0006` before deployment.

## Audit coverage

The existing `audit_events` table remains the authoritative user/security audit instead
of creating a duplicate schema. Login, logout, tenant selection, pairing claims and admin
commands are recorded with tenant and user context. `operational_events` separately records
bounded Connector connect/disconnect lifecycle events. EVCP heartbeat traffic is not stored.
