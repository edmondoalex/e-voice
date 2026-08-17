# Ekonex Home Assistant Integration Standard

Status: Pre-M3 baseline, ready for review  
Scope: future `Ekonex Voice` custom integration (`ekonex_voice`)  
Primary platform: Home Assistant OS (HAOS, formerly Hassio)  
Last reviewed: 2026-08-17

## 1. Purpose and precedence

This document is the mandatory implementation standard for the future Ekonex
Voice Connector. It completes the pre-M3 entry criterion in
[`SPEC_V1.md`](SPEC_V1.md), section 7.1, but does not implement M3.

The order of authority is:

1. current official Home Assistant developer documentation and supported APIs;
2. `docs/SPEC_V1.md` and accepted Ekonex Voice ADRs;
3. this compatibility standard;
4. conventions observed in previous Ekonex components.

Previous products are operational evidence, not an API contract. Most inspected
products are Supervisor add-ons rather than Home Assistant Core integrations, so
their resilience lessons are transferable while their process model, direct REST
or local WebSocket access, MQTT discovery, global state and configuration model
are not.

## 2. Evidence base

### 2.1 Ekonex components actually inspected

The repositories below were cloned from their public default branches and read
at the exact revisions listed. “Add-on” means an independently managed
Supervisor container; it must not be treated as a template for a Core custom
integration.

| Component | Revision inspected | Type and evidence | Relevant finding |
| --- | --- | --- | --- |
| [e-Safe Ksenia Lares 4.0](https://github.com/edmondoalex/e-safe_ksenia_lares_4.0_addon/tree/94e68d5ce2faf8ea64f586d88f75d9f792bb7bc4) | `94e68d5` | HAOS add-on; `config.yaml`, `app/websocketmanager.py`, `app/wscall.py` | Bounded retry delays, explicit WebSocket close/reconnect and HAOS password schemas are useful; several broad exception handlers and payload-oriented logs must not be copied. |
| [e-Safe WS](https://github.com/edmondoalex/e-Safe_ws/tree/7070d01fce0f61b03ac5b00afd059dd100309898) | `7070d01` | Web application/package, not a Core integration | Useful product terminology and UI consistency input; no ConfigEntry lifecycle pattern to reuse. |
| [e_mqtt_safe](https://github.com/edmondoalex/e_mqtt_safe/tree/1643746d3577bc3892eb3ba4c4fcf2cb8d700364) | `1643746` | Small MQTT-oriented repository | Confirms the historical MQTT bridge approach; insufficient lifecycle material and not an architectural basis for Voice. |
| [e-ThermoMind](https://github.com/edmondoalex/e-ThermoMind/tree/a9269115d27257359960e69056efad0d1d130323) | `a926911` | HAOS add-on; `config.yaml`, `backend/ha_client.py` | Clean cancellation propagation and exponential reconnect capped at 30 seconds are useful. Calling HA REST/WebSocket from an add-on and generic service calls are specifically unsuitable for the in-process Voice integration. |
| [eSunMind](https://github.com/edmondoalex/eSunMind/tree/86c80be1469a38f92223ed504fb84f73b18d2770) | `86c80be` | HAOS add-on; config, backend and probe scripts | Strongest reusable evidence for recursive secret redaction, bounded diagnostics and HAOS ingress/configuration. Large global runtime dictionaries and monolithic modules must be discarded. |
| [e-hdl-BusPro-MQTT-addon](https://github.com/edmondoalex/e-hdl-BusPro-MQTT-addon/tree/5285d144ea602c166d16de521525d52823e418b9) | `5285d14` | HAOS MQTT gateway add-on; `app/buspro_gateway.py`, `app/main.py` | Separate gateway lifecycle, explicit start/stop and availability reporting are useful. MQTT discovery identities and add-on process lifecycle do not define Voice ConfigEntry behavior. |
| [e-Therm Plus KS](https://github.com/edmondoalex/e-Therm_Plus_ks/tree/dbb9dad94b972eefe1d552388240a4b52fdf0afc) | `dbb9dad` | HAOS add-on; `config.yaml`, `app/websocketmanager.py`, MQTT watchdog | Useful protections include one reconnect owner, stale-client callback rejection, capped progressive backoff and health data. Unstructured `print`, duplicated reconnect paths, broad catches and raw payload logging must be removed. |
| [ea_CustomComponents / Dahua Event Listener](https://github.com/edmondoalex/ea_CustomComponents/tree/898d0d72b45efac9364a05ab1260a4b6a2262adf/custom_components/dahua_event_listener) | `898d0d7` | Actual custom integration; `__init__.py`, `config_flow.py`, coordinator and platforms | Reusable: UI setup, duplicate-flow abort, platform forwarding, unload and coordinator entities. Normalize: typed runtime data, stable hardware/service ID instead of user name, async I/O, reauth/reconfigure, translations, diagnostics and owned task shutdown. |

This inventory records only code that was actually accessible and inspected. No
claim is made about private, deleted or unshared variants. Repository names in
Issue #4 that differ only in spelling or product shorthand map to the public
repositories above.

### 2.2 Official Home Assistant references

The standard was checked against the current official documentation:

- [Config entries and lifecycle](https://developers.home-assistant.io/docs/config_entries_index/)
- [Config flow, unique IDs, reauth and reconfigure](https://developers.home-assistant.io/docs/core/integration/config_flow/)
- [Integration diagnostics and redaction](https://developers.home-assistant.io/docs/core/integration/diagnostics/)
- [Handling setup failures](https://developers.home-assistant.io/docs/integration_setup_failures/)
- [Backend localization](https://developers.home-assistant.io/docs/internationalization/core/)
- [Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/)

M3 must re-check these pages and the manifest schema against the Home Assistant
version selected for development; this dated review is not permission to freeze
Home Assistant APIs.

## 3. Convention decisions

| Area | Reuse | Normalize for Ekonex Voice | Discard |
| --- | --- | --- | --- |
| HAOS experience | UI-first installation, clear health state, no router changes | Native custom integration installed and configured from Devices & Services | Requiring add-on options, exposed ports, ingress UI or YAML |
| Lifecycle | Explicit start/stop and reconnect ownership | ConfigEntry setup/unload/reload with typed `runtime_data` and HA-owned cleanup callbacks | Global mutable runtime maps, orphan tasks and process-restart recovery |
| Connection | Timeouts, capped backoff, reconnect health, stale-client protection | One async supervisor with exponential full-jitter backoff and cancellable waits | Multiple reconnect loops, blocking sleep, synchronous network I/O in the event loop |
| Configuration | Password fields and bounded numeric settings | Config flow for setup, reauth for credentials, reconfigure for setup data | Editing credentials in options, user-editable identity, YAML setup |
| Identity | Stable external identifiers where available | Cloud `installation_id` as ConfigEntry unique ID; registry identifiers namespaced by `ekonex_voice` | Name, URL, host, pairing code, token or `entry_id` as durable unique ID |
| Availability | Explicit connected/stale state | Integration health derived from authenticated Connector session; entities later use standard availability | Deleting/recreating entries or entities after transient outages |
| Diagnostics | Bounded operational snapshots and recursive redaction | HA diagnostics API plus explicit allowlist and `async_redact_data` | Raw config dumps, tokens, codes, message payloads or personal data |
| Logging | Module loggers, retry reason and health context | Lazy parameterized HA logging with safe identifiers and stable error codes | `print`, credential/payload logging, duplicate retry warnings, exception swallowing |
| Naming | Consistent Ekonex product prefix | Domain `ekonex_voice`, display name `Ekonex Voice`, English code identifiers | Mixed product casing, translated code identifiers and dynamic entity IDs |
| Localization | Italian product terminology | English source strings plus complete `en.json` and `it.json` catalogs | Hard-coded Italian UI text or untranslated error strings |
| Remote control | Explicit product actions in some components | Versioned, allowlisted abstract EVCP operations only | Generic arbitrary HA domain/service execution |

## 4. Mandatory integration standard

### 4.1 Packaging and HAOS baseline

- Domain is `ekonex_voice`; product name is `Ekonex Voice`.
- HAOS is the reference platform for installation, upgrade, restart, network
  loss and diagnostics acceptance tests.
- Setup is UI-only with `config_flow: true`; no `configuration.yaml`, add-on,
  host networking, ingress panel or inbound listener is required.
- M3 must use the current manifest schema and declare only necessary keys and
  dependencies. A cloud-service integration type should be used if still the
  official classification at implementation time.
- Local Home Assistant functionality must remain independent of Ekonex Cloud
  availability.

### 4.2 ConfigEntry lifecycle

The future integration must define a typed ConfigEntry alias whose
`runtime_data` owns one runtime object. That object owns the cloud client,
connection supervisor, task set and unsubscribe callbacks.

`async_setup_entry` must:

1. validate/migrate the entry before creating runtime activity;
2. construct the client from entry data without logging secrets;
3. perform a bounded initial authentication/connectivity check;
4. translate invalid/revoked credentials to `ConfigEntryAuthFailed`;
5. translate temporary network/service failures to `ConfigEntryNotReady`;
6. assign runtime data only after the object is coherent;
7. register update listeners and cleanup with ConfigEntry-owned callbacks;
8. forward only the platforms delivered by the current milestone.

`async_unload_entry` must stop new work, unsubscribe listeners, cancel and await
heartbeat/reconnect tasks, close the cloud session, unload platforms and clear
runtime data. Cancellation must propagate; `CancelledError` must never be folded
into retry handling. Unload is idempotent and bounded. Reload must produce one
connection supervisor and no duplicate listener.

Home Assistant shutdown uses the same close path. The integration must not rely
on interpreter exit, daemon threads or garbage collection. Network I/O is async;
blocking work, if unavoidable, uses HA executor facilities and has an explicit
stop mechanism.

### 4.3 Entry versions and update safety

- Config flow declares `VERSION` and, when needed, `MINOR_VERSION`.
- `async_migrate_entry` handles every supported older schema explicitly and
  returns failure without destroying the previous data when migration cannot be
  completed.
- Credentials and identity live in entry data. User preferences alone belong in
  options.
- Upgrades must preserve `entry_id`, cloud `installation_id`, registry links and
  user customizations.
- A failed update must remain recoverable through retry, reauth, reconfigure or
  rollback to the previous integration release; deleting the entry is not a
  routine recovery instruction.

### 4.4 Config flow and pairing boundary

The M3 config flow consumes the M2 pairing API but must not weaken it:

- request a short-lived pairing session;
- show the human code and expiry without placing either in logs;
- poll using the session polling secret, with bounded timeout and cancellation;
- store only the final Connector credential and cloud installation identity;
- never use the human code, polling secret, user label, URL or flow ID as the
  permanent credential or unique ID;
- abort safely on user cancellation, expiry, duplicate installation or protocol
  incompatibility, cleaning transient polling work.

Forms use stable translation keys. Expected base errors include
`cannot_connect`, `invalid_auth`, `pairing_expired`, `pairing_denied`,
`already_configured` and `unknown`; raw exception text is not user-facing.

### 4.5 Reauthentication and reconfiguration

- Revoked, expired or rejected Connector credentials trigger a ConfigEntry-linked
  reauth flow. Successful reauth updates and reloads the existing entry; it never
  creates a second installation.
- Reauth must set the returned cloud installation ID as unique ID and abort on a
  mismatch. This prevents accidentally moving an entry to another tenant or
  installation.
- Reconfigure is reserved for non-authentication setup data, such as selecting a
  non-production endpoint in an explicitly enabled development build.
- Production cloud endpoint selection is hidden and immutable in normal UI.
- Reconfigure uses the official update/reload-and-abort helper and preserves the
  unique ID. Optional behavior belongs in an OptionsFlow only when it is truly
  optional.

### 4.6 Stable identity and duplicate prevention

- ConfigEntry unique ID is the immutable cloud `installation_id`, assigned only
  after a successful claim and before entry creation.
- Call `async_set_unique_id(installation_id)` and abort if already configured.
- During reauth/reconfigure, require the unique ID to match the target entry.
- One HA installation may not create two active entries for the same Ekonex
  installation.
- Future device registry identifier is `(DOMAIN, installation_id)`. Entity
  unique IDs introduced in M4 derive from stable cloud installation identity and
  stable HA registry identity, never from entity display name or mutable
  `entity_id` alone.
- Device and entity names are user-facing suggestions. Renames never mutate
  registry identity. Registry APIs, not manual registry storage edits, are used.
- M3 creates no synchronized entities; registry behavior that belongs to M4 must
  not be pulled forward.

### 4.7 Connection supervision, timeout and backoff

Exactly one supervisor task owns the outbound cloud connection per entry.

- Every connect, authentication, receive, send, close and heartbeat wait has a
  defined timeout.
- Retry transient DNS, TCP, TLS, timeout, server `5xx` and clean disconnect
  failures.
- Do not retry invalid credentials indefinitely; start reauth. Treat explicit
  unsupported protocol/version as non-retryable and surface repair guidance.
- Use exponential **full jitter**: random delay in `[0, cap]`, where the cap grows
  from about 1 second to at most 60 seconds. Reset only after an authenticated,
  stable session—not merely a TCP connect.
- Prevent concurrent reconnect attempts with a single task/lock owner.
- Heartbeat failure closes the stale transport before reconnect. Callbacks from
  replaced sessions are ignored by generation/session identity.
- On reconnect, authenticate first and then reconcile required state according
  to the milestone protocol. Do not execute or queue arbitrary HA services.
- HA unload/shutdown cancels backoff immediately and awaits the supervisor.

### 4.8 Availability and recovery

Connection state is explicit: `connecting`, `online`, `backing_off`,
`reauth_required`, `protocol_error` or `stopped`. Transient cloud failure marks
the Connector offline but never unloads local integrations or deletes registry
objects. When entities exist in later milestones, they expose availability using
standard HA entity semantics and recover without recreation.

The supervisor should retain only bounded safe health facts such as last
successful connection time, last disconnect error code, retry count and next
retry delay. It must not retain plaintext message/credential history for
diagnostics.

### 4.9 Diagnostics and secret redaction

Implement config-entry diagnostics through Home Assistant's diagnostics API.
Return an allowlisted structure, then apply `async_redact_data` as defense in
depth. At minimum redact:

- Connector credential and authorization headers;
- pairing code, polling secret and encrypted credential envelope;
- cookies, tokens, passwords and URL query credentials;
- tenant/user personal data and precise location;
- raw inbound/outbound protocol payloads unless reduced to a documented safe
  schema.

Allowed diagnostic fields include integration/HA version, entry schema version,
connection state, safe installation public identifier, protocol version,
bounded retry/latency counters and timestamps. Add automated canary secrets in
tests and recursively assert that neither keys nor values leak. Diagnostics must
remain useful with redaction and must be bounded in size.

### 4.10 Logging

- Use `_LOGGER = logging.getLogger(__name__)` and lazy `%s` interpolation.
- Use `debug` for normal connection detail and retries already managed by HA,
  `info` for meaningful lifecycle transitions, `warning` for actionable degraded
  behavior, and `error` only when an operation has failed and is not already
  reported by HA lifecycle machinery.
- Emit stable safe context where useful: `entry_id`, public installation ID,
  session generation, operation/error code and retry delay. Do not log tenant
  names or user identifiers by default.
- Never log credentials, pairing values, headers, cookies, full URLs with query
  strings, raw payloads or exception representations that may contain them.
- One layer owns each failure log. Reconnect loops must not create warning storms;
  transition logging and rate limiting are required.

### 4.11 Naming and translations

Code, modules, identifiers and canonical error keys are English. Product naming
is exactly:

- domain/package: `ekonex_voice`;
- manifest and UI title: `Ekonex Voice`;
- runtime types: descriptive names such as `EkonexVoiceConfigEntry`,
  `EkonexVoiceRuntimeData` and `EkonexVoiceConnection`;
- log prefixing is provided by the module logger, not handwritten tags.

`strings.json` is the source catalog. `translations/en.json` and
`translations/it.json` must contain matching keys for config steps, aborts,
errors, reauth, reconfigure and future repair issues. User-visible Python string
literals are prohibited when a translation key is supported. Placeholders use
semantic names, have identical sets in both languages and never contain secrets.
Italian terminology should consistently use “installazione”, “collega”,
“ricollega” and “credenziale”; English uses “installation”, “connect”,
“reconnect” and “credential”.

### 4.12 Error taxonomy

The client library exposes typed, provider-neutral errors:

- authentication/revocation → `ConfigEntryAuthFailed` and reauth;
- temporary network/cloud/timeout → `ConfigEntryNotReady` during setup, or
  supervised reconnect at runtime;
- invalid local/config input → translated config-flow field/base error;
- duplicate or identity mismatch → translated abort, without revealing another
  tenant or installation;
- incompatible protocol or permanent account state → non-retryable setup/repair
  error with a stable translation key;
- cancellation → always re-raised;
- unexpected internal error → safe `unknown` UI result and one redacted
  exception log.

User messages must prescribe a recovery action without exposing HTTP bodies,
tenant existence or secret-bearing exception detail.

## 5. Minimum test gate for the future Connector

M3 is not complete unless automated tests cover at least:

### Config flow

- successful pairing creates one entry with the cloud installation ID as unique
  ID and the final credential—not the human code—as stored auth material;
- duplicate installation and concurrent duplicate flow abort;
- invalid input, cannot-connect, expiry, denial, cancellation and unknown errors;
- no secret appears in flow results, logs or snapshots;
- reauth updates/reloads the same entry and rejects identity mismatch;
- reconfigure updates/reloads the same entry, preserves identity and creates no
  duplicate;
- English and Italian catalogs have equal required keys and placeholders.

### Lifecycle and migration

- setup success, temporary setup retry, invalid-auth reauth and permanent error;
- unload closes client, unsubscribes callbacks, cancels/awaits tasks and unloads
  platforms;
- reload leaves exactly one connection and listener set;
- HA shutdown uses the same cleanup path;
- every supported ConfigEntry version migrates without identity/credential loss;
  failed migration leaves the old data recoverable.

### Connection and recovery

- disconnect, DNS/TLS/timeout and heartbeat failure reconnect with bounded full
  jitter;
- invalid auth does not enter an infinite retry loop;
- only one reconnect task exists under concurrent failure signals;
- successful stable authentication resets backoff;
- cancellation interrupts connect, receive and sleep promptly;
- stale-session callbacks are ignored;
- HA remains responsive and local behavior unaffected while cloud is offline.

### Security and diagnostics

- diagnostics recursively redact canary credential, code, polling secret,
  headers, query parameters and personal data;
- logs contain no canary secret at any level or failure path;
- cross-installation identity mismatch is denied without existence disclosure;
- no generic domain/service remote execution surface exists;
- diagnostic collections and queues are bounded.

Run Home Assistant integration tests with `pytest-homeassistant-custom-component`
or the then-current official custom-integration test harness, plus Ruff, mypy and
Home Assistant manifest/translation validation. Perform an HAOS acceptance pass
for fresh UI install, restart, upgrade, offline/online recovery, reauth,
reconfigure, diagnostics download and removal. CI simulation does not replace
the HAOS pass.

## 6. M3 entry checklist

Before writing `custom_components/ekonex_voice`:

- [ ] This document and PR #5 are approved and merged.
- [ ] Official HA manifest, ConfigEntry, config-flow, diagnostics and translation
      documentation is rechecked for the selected HA release.
- [ ] M3 implementation plan maps each standard rule to code and tests.
- [ ] Pairing API boundary and error mapping are agreed without adding M2 scope.
- [ ] HAOS test version and manual acceptance procedure are recorded.
- [ ] No entity sync, Alexa behavior or generic remote service execution is
      included in M3.

## 7. ADR assessment

No new ADR is required by this analysis. The standard applies the architecture
already mandated by `SPEC_V1.md` and ADR-0002 (outbound connection), ADR-0004
(safe command protocol), and ADR-0005 (Home Assistant as source of truth). An ADR
must be added later only if implementation proposes a genuine architectural
departure, not merely to restate Home Assistant lifecycle conventions.
