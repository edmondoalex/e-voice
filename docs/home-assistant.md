# Ekonex Voice Home Assistant Connector

Status: M3 foundation, ready for review

The native integration lives in `custom_components/ekonex_voice` and follows
[`EKONEX_HA_STANDARD.md`](EKONEX_HA_STANDARD.md). HAOS/Home Assistant OS is the
primary platform.

## M3 scope

Implemented:

- current custom-integration manifest and UI-only config flow;
- M2 pairing client boundary with transient code/polling material;
- ConfigEntry identity based on immutable cloud `installation_id`;
- reauthentication that updates the existing entry and rejects identity moves;
- typed runtime data and deterministic setup/unload/reload cleanup;
- one cancellation-safe connection supervisor with capped full-jitter backoff;
- recursively redacted, bounded diagnostics;
- complete English and Italian runtime translation catalogs.

Deferred:

- EVCP WebSocket transport and heartbeat wire protocol;
- entity inventory, delta/state synchronization and registry entities;
- command execution and service mapping;
- Alexa and portal behavior.

There is no YAML setup, inbound listener, reverse proxy, generic HA service
executor, or connection from the integration to HA's local `/api/websocket`.

## Pairing HTTP boundary

The client isolates the Connector HTTP surface under `/connector/v1`:

- `POST /pairing/sessions` creates a short-lived session;
- `GET /pairing/sessions/{session_id}` polls with the transient `Pairing`
  authorization credential;
- `POST /auth/validate` validates the final Connector bearer credential.

Human code and polling secret exist only in the in-memory config flow. The
ConfigEntry persists the cloud URL, installation ID, final Connector credential
and safe display names. Response bodies and secret-bearing exceptions are never
logged. Production deployment of the cloud routes is outside M3.

## Config flow behavior

1. Add `Ekonex Voice` from Devices & Services.
2. Home Assistant requests and displays the one-time code and expiry.
3. Claim the code in the Ekonex portal.
4. Select Submit to check the claim. Pending/connectivity states can be checked
   again without creating another entry.
5. On success, Home Assistant stores only durable claim material and prevents a
   duplicate `installation_id`.

Revoked credentials trigger ConfigEntry reauth. Reauth requires a new M2 claim
for the same installation. Reconfigure is intentionally not exposed because M3
has no justified non-authentication setup field; production endpoint selection
is not user-editable.

## Local validation limitation

The repository workstation is Windows. Home Assistant 2026.2.3 imports the
POSIX-only `fcntl` module in its runner, so the HA test harness cannot start
natively on this host. Backend M1/M2 tests run locally with plugin autoload
disabled; the complete suite runs in Linux GitHub Actions.

This does not replace the HAOS acceptance pass below.

## HAOS manual acceptance procedure

Use a disposable, fully backed-up HAOS instance running the Home Assistant
version selected by CI/release.

1. Copy `custom_components/ekonex_voice` into `/config/custom_components/` and
   restart Home Assistant.
2. Confirm `Ekonex Voice` appears under Add integration and that no YAML, add-on,
   port or ingress configuration is requested.
3. Start setup with the M2 endpoints available. Confirm the human code and expiry
   are shown and no polling/final credential appears in logs.
4. Submit before claim and confirm the pending message is recoverable. Claim in
   the portal, submit again and confirm exactly one entry titled
   `<tenant> / <installation>`.
5. Add the same cloud installation again and confirm `already_configured`
   without disclosure of tenant details.
6. Restart Home Assistant. Confirm the entry loads once and there is no duplicate
   connection task or listener.
7. Disable/re-enable and reload the entry. Confirm cleanup and one supervisor.
8. Disconnect Internet/cloud, restart HA, and confirm local entities and
   automations continue while the entry retries without log flooding. Restore
   connectivity and confirm recovery.
9. Revoke the Connector credential. Confirm reauth; complete a new claim for the
   same installation and verify the same entry is updated. Confirm a different
   installation is rejected.
10. Download diagnostics and recursively search for the human code, polling
    secret, Connector credential, authorization headers and canary values; none
    may be present.
11. Remove the entry and shut down HA. Confirm there are no pending-task,
    unclosed-session or callback errors.
12. Repeat with English and Italian UI and verify setup, errors, abort and reauth.

M3 acceptance does not require entity creation, a production EVCP WebSocket or
Alexa control.
