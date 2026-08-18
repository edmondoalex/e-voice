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

The synchronous adapter supports Discovery, control responses, Alexa
`ReportState`/`StateReport`, EndpointHealth and error responses. Properties are
retrievable. Proactive event-gateway authorization and ChangeReport publication
are not advertised by this M7 adapter; they remain outside this synchronous
contract and must not be enabled in the developer console until implemented.

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
5. Keep “Send Alexa Events” disabled for this synchronous M7 release.

`GET /oauth/authorize` is behind the existing Ekonex authentication boundary.
The current authenticated user is conveyed internally as `X-Ekonex-User-ID`;
the public ingress/proxy must remove caller-supplied copies and set it only after
login and consent. The selected tenant must be an actual user membership.
Connector credentials are never reused or exposed. OAuth grants and tokens are
stored only as SHA-256 digests. Unlink with `POST /oauth/revoke`; this revokes
Alexa independently of HA pairing.

## Capability matrix

| HA domain | Alexa interfaces | M6 operations |
| --- | --- | --- |
| light | Power, Brightness; Color/ColorTemperature only when M5 attributes support them | on/off, brightness, RGB, Kelvin |
| switch | PowerController | on/off |
| cover | RangeController position | position; open/close remain accepted only when advertised by a future cover interface revision |
| climate | ThermostatController | target temperature, thermostat mode |
| fan | PowerController, PercentageController | on/off, percentage |
| scene | SceneController | activate |

Locks, alarms, cameras, scripts, buttons, numbers, selects and arbitrary sensors
are never discovered. No Alexa field can choose an HA service, domain, entity ID
or target. Tombstoned M5 entities immediately disappear and are no longer
controllable. Endpoint ID is derived from the immutable cloud entity UUID, so HA
name/entity_id changes update presentation without duplicating identity.

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

Amazon developer-console configuration, Lambda/HTTPS reachability, real Alexa
account linking, voice utterances and certification cannot run in repository CI.
