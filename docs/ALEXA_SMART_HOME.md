# Ekonex Voice Alexa Smart Home v3

M7 implements one Alexa Smart Home skill for every Ekonex tenant. Alexa calls
Ekonex Cloud; it never connects to Home Assistant. Discovery and state come only
from the current M5 opt-in inventory. Controls resolve the opaque stable endpoint
to its tenant-owned entity and use only the typed M6 dispatcher.

## Amazon requirements verified for M7

The implementation was checked on 2026-08-18 against Amazon's current official
Smart Home API, Discovery v3, account-linking authorization-code and interface
documentation. Smart Home skills require API payload version 3 and account
linking. Authorization codes are one-use and expire after five minutes. Access
tokens expire after one hour by default; refresh tokens rotate on every use.
PKCE S256 is accepted when Amazon supplies a challenge. All regional redirect
URLs displayed by the developer console must be registered exactly.

The adapter supports Discovery, control responses, Alexa
`ReportState`/`StateReport`, EndpointHealth, error responses and proactive
`Alexa.ChangeReport`. All reportable properties are retrievable and proactively
reported. `Alexa.Authorization.AcceptGrant` exchanges the short Amazon grant at
LWA, encrypts the resulting customer tokens at rest, refreshes them before
expiry/on HTTP 401, and sends to the configured regional Event Gateway. M5 state
deltas trigger reports; identical property snapshots are durably suppressed and
HTTP 401/429/503 responses receive three bounded attempts.

## Proactive discovery and portal observability

After `Alexa.Authorization/AcceptGrant`, Ekonex exchanges Amazon's one-use grant with LWA and
stores the customer access/refresh tokens encrypted in `alexa_event_authorizations`. These Amazon
tokens authenticate Ekonex **to Alexa** and are distinct from the Ekonex BearerToken Alexa sends
with directives. They are refreshed through LWA before expiry or after an Event Gateway HTTP 401;
neither tokens nor event bodies are logged or displayed.

Every committed inventory full/delta, relevant state metadata update, and cloud voice-name edit
reconciles the installation against the endpoint representation produced by the existing
`discovery_endpoint()` mapper. A per-account delivery ledger stores only endpoint ID and a SHA-256
representation fingerprint. New or materially changed representations generate
`Alexa.Discovery.AddOrUpdateReport`; endpoints no longer eligible generate
`Alexa.Discovery.DeleteReport` with their previously published endpoint ID. Unchanged fingerprints
produce no event. A failed delivery does not fail HA/EVCP synchronization and remains eligible for
a later bounded retry.

For Italy/Europe configure:

```dotenv
EKONEX_ALEXA_EVENT_GATEWAY_URL=https://api.eu.amazonalexa.com/v3/events
EKONEX_ALEXA_LWA_CLIENT_ID=<Login with Amazon security profile client ID>
EKONEX_ALEXA_LWA_CLIENT_SECRET=<secret-manager value>
EKONEX_ALEXA_TOKEN_ENCRYPTION_KEY=<stable high-entropy production secret>
```

In the Alexa Developer Console, enable **Send Alexa Events** and confirm that the Smart Home skill
receives `AcceptGrant`. The LWA security profile configured above must be the one associated with
the skill. Regional account-linking redirect URLs remain configured as described below. No Lambda
change is required: proactive reports are emitted by the Ekonex Cloud backend directly to the
regional Alexa Event Gateway.

The installation portal section **Alexa - ultima sincronizzazione** shows the last manual
Discovery snapshot and its endpoint names/IDs/domains/diff, plus the most recent
AddOrUpdateReport/DeleteReport endpoint, result and timestamp. It reads tenant-scoped snapshots and
redacted audit metadata only. Apply Alembic migration `20260820_0009` before deploying.

The leading **Ultima attività Alexa** line compares those already-loaded timestamps and reports the
newest complete Discovery, AddOrUpdateReport or DeleteReport. It is a presentation-only summary:
`Ultima Discovery` continues to mean only the last complete Discover.Response snapshot, and no
additional inventory, query or persistence is introduced.

The portal labels the complete snapshot as historical and explains that its entries and comparison
badges refer only to the preceding complete Discovery. **Dispositivi attualmente presenti in
Alexa** remains derived from active rows in the existing proactive-delivery ledger, so it is the
current operational view. These labels do not alter snapshot, report or ledger semantics.

End-to-end acceptance:

1. Enable/relink the development skill so Ekonex receives a successful `AcceptGrant`.
2. Expose a new entity in e-Control and wait for inventory sync; verify it appears in Alexa without
   invoking manual discovery and the portal shows a successful AddOrUpdateReport.
3. Rename its effective voice name; verify Alexa and the portal update while endpointId is stable.
4. Change only state; verify no AddOrUpdateReport unless the change alters advertised capabilities.
5. Remove Ekonex exposure; verify a successful DeleteReport and removal from Alexa.
6. Temporarily point the gateway to a controlled failing endpoint in staging; verify sync still
   commits, delivery is audited as error, secrets are absent, and a later reconcile retries it.

## Developer console

1. Create a Smart Home skill (not a Custom Skill) and select payload version 3.
2. Set the HTTPS/Lambda endpoint to forward the unmodified directive body to
   `POST https://<cloud>/alexa/v1/directive`.
3. Configure Authorization Code Grant:
   - Authorization URI: `https://<cloud>/oauth/authorize`
   - Access Token URI: `https://<cloud>/oauth/token`
   - client ID/secret: secret-manager values matching the `EKONEX_ALEXA_*`
     environment settings
   - scope: `alexa:smart_home`
   - client authentication: HTTP Basic or request body
4. Copy every Amazon-provided regional redirect URL into
   `EKONEX_ALEXA_REDIRECT_URIS` as an exact comma-separated allowlist.
5. Enable “Send Alexa Events” so Alexa issues `AcceptGrant`, and configure the
   LWA client credentials and region-matched Event Gateway URL.

## AWS Lambda directive adapter

The deployable standard-library-only handler is
`aws_lambda/alexa_smart_home/lambda_function.py`. Set
`EKONEX_VOICE_BACKEND_URL=https://voice.e-control.tech`; the handler posts the unmodified
Alexa directive to `/alexa/v1/directive`. The BearerToken remains inside the directive body and
is validated only by Ekonex Cloud. The cloud uses the existing tenant-owned `Entity` inventory,
opt-in/tombstone rules, centralized voice names and capability mapper to build
`Alexa.Discovery.Discover.Response`; the Lambda has no parallel device model.

The Lambda maps invalid, revoked and expired cloud tokens to the corresponding Alexa v3
authorization errors, maps rate limiting explicitly and uses `INTERNAL_ERROR` for transport or
unexpected backend failures. Logs contain only directive namespace/name and bounded status
metadata, never request bodies, access tokens or credentials. Exact packaging, AWS CLI update,
runtime/environment and alias instructions are in
`aws_lambda/alexa_smart_home/README.md`.

`GET /oauth/authorize` serves the browser account-linking flow directly. Customers
authenticate with their existing e-Control email/password through the same
`PortalAuthenticationService` used by the pairing portal; no Cognito account or
parallel identity store is required. Sessions remain opaque and server-side, with
secure cookies and login rate limiting. A user with one membership proceeds directly;
a user with multiple memberships chooses only among their active tenants through a
CSRF-protected form. The authorization code redirect preserves Amazon's `state` value.

The legacy trusted-header path remains available for controlled internal callers, but
the public browser flow does not require reverse-proxy identity headers. Every selected
tenant is revalidated against the authenticated user's memberships and fails closed.
Connector credentials are never reused or exposed. OAuth grants and tokens are stored
only as SHA-256 digests. Unlink with `POST /oauth/revoke`; this revokes Alexa
independently of HA pairing.

## Capability matrix

| HA domain | Alexa interfaces | M6 operations |
| --- | --- | --- |
| light | Power, Brightness; Color/ColorTemperature only when M5 attributes support them | on/off, brightness, RGB, Kelvin |
| switch | PowerController | on/off |
| cover | Per-entity Discrete: ModeController `Blinds.Position`; Percentage: RangeController `Blind.Lift`; Hybrid: both, with semantics on only one controller | typed open/close and/or absolute/relative position |
| climate | ThermostatController | target temperature, thermostat mode |
| fan | PowerController, PercentageController | on/off, percentage |
| scene | SceneController | activate |

Locks, alarms, cameras, scripts, buttons, numbers, selects and arbitrary sensors
are never discovered. No Alexa field can choose an HA service, domain, entity ID
or target. Tombstoned M5 entities immediately disappear and are no longer
controllable. Endpoint ID is derived from the immutable cloud entity UUID, so HA
name/entity_id changes update presentation without duplicating identity.

### Cover exposure modes

The entity edit page offers a persisted Alexa mode for each `cover`: **Discrete** (open/close),
**Percentage** (absolute
position 0–100), or **Hybrid** (both capability families). `Automatico` is the safe default and
derives the least surprising publishable mode solely from `supported_features`: percentage when
set-position exists (preserving the established range behavior), otherwise discrete when
open+close exist. Hybrid is an explicit opt-in when both feature families are available.
An incompatible explicit mode is rejected in the portal; if HA later removes a required feature,
the endpoint is omitted until its mode or features become compatible instead of advertising a
command that cannot execute.

Hybrid discovery maps open/close/raise/lower semantics only on `ModeController`, avoiding the
semantic phrase collision Amazon forbids across generic controllers on one endpoint. The range
controller remains directly addressable for position utterances. A mode change preserves the
entity-derived endpoint ID but changes its Discovery representation fingerprint, so the existing
proactive reconciliation emits `AddOrUpdateReport` only when needed. The implementation follows
Amazon's current blinds/shades device template and generic-controller semantic uniqueness rules.
Apply Alembic migration `20260820_0011` before deploying.

Amazon's current blinds/shades template defines the discrete `Blinds.Position` controller with
exactly two modes: `Position.Up` and `Position.Down`. Alexa Smart Home does not define a Stop
directive for window treatments; media `PlaybackController.Stop` and cooking
`TimeHoldController.Hold` are not valid substitutes. Consequently Ekonex does not advertise a
custom third stop mode, because doing so turns the standard blinds controller into a generic
selector and can make open/close utterance routing unreliable. Stop remains available in the
e-Control portal and EVCP when HA supports it, but not as an Alexa cover utterance.
The controller includes Amazon's canonical Open/Close action mappings and Open/Closed state
mappings; global Alexa assets provide localized mode vocabulary, including `it-IT`, without
custom localized controller values.

`AddOrUpdateReport` updates the endpoint representation while preserving `endpointId`. Amazon can
retain cached controller metadata, especially after a previously incompatible representation;
after deploying this correction, run one complete device discovery (or remove and rediscover the
device) to replace that cached model. Ekonex cannot force the Alexa app to render three direct
buttons: the app UI is selected by Alexa from the declared standard capability.

## Manual acceptance

1. Pair HA and opt in one entity for every supported domain.
2. Enable the development skill, log in, choose the tenant and grant consent.
3. Discover devices; verify only that tenant's current M5 inventory appears.
4. Exercise power, brightness/color, cover position, thermostat target/mode,
   fan percentage and scene activation; verify HA and the Alexa response state.
5. Rename an entity in HA, let M5 synchronize, rediscover and verify the same
   endpoint has the new friendly name.
6. Remove Ekonex exposure, let M5 tombstone it, rediscover, and verify discovery
   and commands no longer expose it.
7. Unlink/revoke Alexa and verify directives fail while the Connector remains
   paired and connected.
8. Change state physically/in HA and verify a ChangeReport arrives within three
   seconds in Amazon's Smart Home State Reporter; repeat the same state and
   verify it is not duplicated. Exercise a temporary 401/429/503 in staging to
   verify refresh and bounded retry.

Amazon developer-console configuration, Lambda/HTTPS reachability, real Alexa
account linking, voice utterances and certification cannot run in repository CI.
