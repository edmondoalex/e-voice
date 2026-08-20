# Ekonex Voice Home Assistant Connector

Version 0.1.7 includes the current entity icon in inventory metadata when available. The field is
optional for EVCP compatibility and never changes registry identity or exposure selection.

## M5 opt-in exposure acceptance

On HAOS, open Ekonex Voice configuration options. A fresh entry must show no
selected items and the cloud inventory must remain empty. Select devices and
standalone entities from multiple domains, save, and verify immediate cloud reconciliation. Then
deselect each and verify tombstones without restarting Home Assistant.

The installer manually creates the dedicated `Ekonex Voice` label in Home
Assistant, then explicitly selects it in the integration options. The integration
never creates or discovers it by name. Its stable label registry ID is persisted.
Assign/remove it on a device and an entity, rename the label, and verify
reconciliation and union semantics with UI selection. Rename an exposed entity
through its editable HA `Nome` field and verify the cloud name changes while the
stable cloud identity does not. Also test a reconnect during a large inventory,
restart/reload, bursty state changes, diagnostics download, and removal.
No credential or unrestricted attribute may appear in logs or diagnostics. To
disable label-based exposure, clear the selected label in the options flow; the
label itself is not deleted from Home Assistant.

## Inventory synchronization diagnostics

An empty `entities` table is expected immediately after pairing: exposure is
strictly opt-in. Open **Settings → Devices & services → Ekonex Voice →
Configure**, then select devices and/or individual entities. Alternatively,
manually create a Home Assistant label, select its stable label in the Ekonex
Voice options, and assign that label to authorized entities or devices. Saving
options reloads the entry and sends an immediate full snapshot.

Safe Home Assistant logs record the cloud revision and entity/batch counts for
each full snapshot; they never record entity payloads, credentials or session
identifiers. Integration diagnostics expose the selected device/entity counts,
whether a label is configured, last full revision/count, last state count and a
bounded send error code. Cloud logs record only EVCP message type, revision and
counts, followed by a persistence confirmation. Therefore:

- `entities=0` with a logged `inventory_full ... entities=0` means no entity is
  currently authorized, not a transport failure;
- a WebSocket connection without the snapshot log means the HA inventory start
  failed before or during send;
- a cloud “batch received” without “synchronization applied” identifies an
  incomplete multi-batch snapshot;
- `STALE_REVISION` means reconnect negotiation and cloud revision diverged; the
  Connector closes and performs a full resync from the revision in `hello_ack`.

On reconnect the Connector always sends `inventory_full`, including an explicit
empty snapshot. Registry/label changes trigger another full reconciliation;
state changes are coalesced into `state_update`. Removing final authorization
tombstones the cloud entity. An unavailable HA state remains authorized but is
persisted with `available=false` and no current state value.

Status: M5 entity synchronization, ready for review

## M6 safe command mappers

M6 controls only entities still authorized by the M5 UI/label union. The
Connector resolves stable registry identity to the current entity ID and uses
this fixed map:

| Domain | Allowed operations and validation |
| --- | --- |
| `light` | power, brightness 0–255, RGB and Kelvin within advertised color capabilities |
| `switch` | power on/off |
| `cover` | open, close, stop and position 0–100 when feature flags allow |
| `climate` | target temperature, HVAC mode and power within advertised bounds/modes/features |
| `fan` | power and percentage 0–100 when feature flags allow |
| `scene` / `script` | activate the resolved entity only; no variables or arbitrary data |
| `button` | press |
| `number` | value within advertised minimum/maximum |
| `select` | one advertised option |

Other exposed domains are read-only in M6. Missing, disabled, unavailable,
unexposed, unsupported and invalid targets produce safe results without an HA
action call. Calls have an eight-second ceiling. Success does not synthesize
state; resulting HA events converge through M5.

For HAOS acceptance, exercise valid and invalid boundaries for every mapper.
Rename an entity ID, remove its final authorization, make it unavailable and
reconnect during a command. Confirm stable resolution, no action for rejected
targets, one execution for duplicates, M5 state convergence, clean unload and no
command value, exception detail or secret in logs/diagnostics.

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
