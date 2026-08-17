# EKONEX VOICE — MASTER SPECIFICATION FOR CODEX
Version: 1.0
Status: Architecture baseline / implementation plan
Primary language: Python
Target: Multi-tenant Alexa ↔ Home Assistant control platform
Repository: `ekonex-voice`

---

# 0. EXECUTIVE SUMMARY

Ekonex Voice is a multi-tenant platform owned and operated by Ekonex that exposes selected Home Assistant entities to Amazon Alexa through ONE Ekonex Alexa Smart Home integration.

The customer must NOT need:
- a custom Alexa skill per installation;
- an AWS Lambda per customer;
- port forwarding;
- a public Home Assistant URL;
- YAML configuration;
- manual Alexa endpoint creation;
- access to Amazon Developer Console.

The customer experience must be:

1. Installer adds `Ekonex Voice` integration to Home Assistant.
2. Home Assistant displays a one-time pairing code.
3. Installer claims the installation in the Ekonex portal.
4. Ekonex Cloud receives the HA entity inventory.
5. Installer chooses which entities are exposed to Alexa.
6. Customer enables the single public/private `Ekonex Voice` Alexa Smart Home integration.
7. Customer logs into Ekonex once.
8. Alexa discovers the customer's enabled devices.
9. Customer says native commands such as:
   - "Alexa, accendi luce cucina"
   - "Alexa, spegni luce cucina"
   - "Alexa, apri tenda soggiorno"
   - "Alexa, chiudi tenda soggiorno"
   - "Alexa, imposta soggiorno a 21 gradi"
10. Command travels:
    Alexa → Ekonex Cloud → outbound connector session → Home Assistant → physical device.
11. Home Assistant remains the local automation engine. Ekonex Voice is the cloud voice/control layer.

Ekonex Voice MUST be designed from day one for multiple customers and strict tenant isolation.

---

# 1. PRODUCT PRINCIPLES

## 1.1 One Alexa integration for every customer

There MUST be one Ekonex Voice Alexa Smart Home integration, not one skill per customer.

Account linking identifies the Ekonex customer/tenant.

Concept:

Alexa account
    ↓
Ekonex OAuth account
    ↓
Tenant
    ↓
Installation(s)
    ↓
Published endpoints

A customer may eventually own multiple Home Assistant installations.

## 1.2 Ekonex owns the cloud layer

Ekonex Voice is responsible for:

- customer identity;
- tenant identity;
- Home Assistant installation registration;
- entity inventory;
- Alexa exposure policy;
- Alexa display names;
- Alexa endpoint identity;
- directive routing;
- state synchronization;
- diagnostics;
- command audit;
- security policy;
- connector health;
- account linking.

## 1.3 Home Assistant remains the automation engine

Home Assistant remains responsible for:

- physical integrations;
- automations;
- scripts;
- scenes;
- local logic;
- alarms;
- thermostatic logic;
- device availability;
- physical device state.

Do NOT move customer automation logic to Ekonex Cloud.

Example:

Bad:
Alexa → cloud contains full shutter automation logic.

Good:
Alexa → Ekonex Voice → `cover.open_cover` on HA.

## 1.4 Local operation must survive cloud outage

If Internet or Ekonex Cloud is unavailable:

- Home Assistant continues operating locally.
- HA automations continue.
- local app/dashboard operation continues.
- Alexa cloud control through Ekonex is temporarily unavailable.

Cloud failure MUST NOT break local Home Assistant operation.

## 1.5 No inbound connection to customer network

The customer router must require no configuration.

Ekonex Connector initiates and maintains an outbound TLS WebSocket connection:

HA → `wss://api.ekonex.../connector/v1/ws`

No inbound NAT.
No port forwarding.
No reverse proxy on customer premises.
No customer TLS certificate.

---

# 2. CURRENT PLATFORM ASSUMPTIONS

Implementation must follow current official platform behavior and must NOT rely on undocumented or deprecated APIs.

Amazon:
- Alexa Smart Home integrations are currently described by Amazon as Smart Home add-ons.
- Discovery is based on `Alexa.Discovery`.
- device state changes should be proactively reported when supported.
- OAuth 2.0 account linking is required for this architecture.
- endpoint additions/removals/updates must use supported Alexa discovery/event mechanisms.

Home Assistant:
- HAOS/Home Assistant OS (historically also called Hassio) is the primary and
  reference deployment platform for the Ekonex integration;
- setup must be UI based through a config flow.
- runtime state must use current config-entry patterns.
- integration must support unload/reload.
- diagnostics must redact secrets.
- tests must cover config flow and failure recovery.
- avoid YAML configuration unless explicitly required.

Before implementing an Alexa capability or HA API surface, Codex MUST verify it against current official documentation.

---

# 3. HIGH-LEVEL ARCHITECTURE

```text
                      AMAZON
                  Alexa Smart Home
                        │
                        │ HTTPS directives/events
                        ▼
              ┌─────────────────────┐
              │ EKONEX VOICE CLOUD  │
              │                     │
              │ OAuth / Accounts    │
              │ Tenant isolation    │
              │ Alexa adapter       │
              │ Entity registry     │
              │ Command router      │
              │ Event gateway       │
              │ Audit / diagnostics │
              └──────────┬──────────┘
                         │
                         │ WSS TLS outbound
                         │
            ┌────────────┴────────────┐
            │                         │
            ▼                         ▼
   Home Assistant A           Home Assistant B
   Ekonex Connector           Ekonex Connector
            │                         │
            ▼                         ▼
      local devices              local devices
```

---

# 4. MONOREPO

Repository:

`ekonex-voice`

Structure:

```text
ekonex-voice/
├── README.md
├── LICENSE
├── .gitignore
├── .editorconfig
├── .env.example
├── docker-compose.yml
├── pyproject.toml
├── docs/
│   ├── SPEC_V1.md
│   ├── architecture.md
│   ├── protocol.md
│   ├── security.md
│   ├── alexa.md
│   ├── home-assistant.md
│   ├── onboarding.md
│   ├── operations.md
│   └── adr/
├── apps/
│   ├── cloud_api/
│   │   ├── app/
│   │   └── tests/
│   ├── alexa_adapter/
│   │   ├── app/
│   │   └── tests/
│   └── admin_web/
├── custom_components/
│   └── ekonex_voice/
│       ├── __init__.py
│       ├── manifest.json
│       ├── config_flow.py
│       ├── const.py
│       ├── models.py
│       ├── client.py
│       ├── connection.py
│       ├── entity_inventory.py
│       ├── command_executor.py
│       ├── diagnostics.py
│       ├── strings.json
│       └── translations/
│           ├── en.json
│           └── it.json
├── packages/
│   └── shared/
│       ├── schemas/
│       ├── protocol/
│       └── constants/
├── migrations/
├── scripts/
├── tests/
│   ├── integration/
│   └── e2e/
└── .github/
    ├── workflows/
    ├── ISSUE_TEMPLATE/
    └── pull_request_template.md
```

Keep transport, Alexa mapping, Home Assistant logic, persistence and UI separated.

---

# 5. RECOMMENDED TECHNOLOGY STACK

## Backend

- Python 3.13
- FastAPI
- Pydantic v2
- SQLAlchemy 2.x
- Alembic
- PostgreSQL
- Redis
- async WebSocket handling
- httpx
- structlog or equivalent structured logging
- pytest
- pytest-asyncio
- ruff
- mypy

## Admin frontend

- React
- TypeScript
- Vite
- TanStack Query
- accessible component library

Do NOT over-design the admin UI in MVP.

## Infrastructure

Development:
- Docker
- Docker Compose

Production later:
- reverse proxy / ingress
- PostgreSQL
- Redis
- cloud object/log storage if needed
- secret manager
- CI/CD GitHub Actions

Production design must support horizontal scale of API workers.

---

# 6. DOMAIN MODEL

## 6.1 Dealer

Ekonex may later allow multiple installers/dealers.

`dealers`
- `id UUID PK`
- `name`
- `slug`
- `status`
- `created_at`
- `updated_at`

For V1, Ekonex itself can be the only dealer but the DB should not prevent future dealers.

## 6.2 User

`users`
- `id UUID PK`
- `email`
- `password_hash`
- `status`
- `created_at`
- `updated_at`
- `last_login_at`

Roles should NOT be stored as one free-form string if future RBAC is expected.

Suggested:
- owner
- dealer_admin
- installer
- customer_admin
- customer_user
- support_readonly

## 6.3 Tenant

Represents a customer/account boundary.

`tenants`
- `id UUID PK`
- `dealer_id FK`
- `name`
- `slug`
- `status`
- `created_at`
- `updated_at`

Tenant is the primary security isolation boundary.

## 6.4 Membership

`tenant_memberships`
- `id`
- `tenant_id`
- `user_id`
- `role`
- `created_at`

## 6.5 Installation

Represents one Home Assistant instance.

`installations`
- `id UUID PK`
- `tenant_id FK`
- `name`
- `public_id`
- `status`
- `connector_version`
- `ha_version`
- `ha_installation_type`
- `last_seen_at`
- `created_at`
- `updated_at`
- `revoked_at`

Never store plaintext device secret.

## 6.6 Connector credential

`connector_credentials`
- `id`
- `installation_id`
- `secret_hash`
- `created_at`
- `last_used_at`
- `revoked_at`

Support credential rotation.

## 6.7 Entity

`entities`
- `id UUID PK`
- `installation_id FK`
- `ha_entity_id`
- `ha_domain`
- `friendly_name`
- `area_id`
- `area_name`
- `device_class`
- `supported_features`
- `state`
- `attributes_json`
- `available`
- `last_changed_at`
- `last_seen_at`
- `created_at`
- `updated_at`
- `deleted_at`

Unique:
`(installation_id, ha_entity_id)`

## 6.8 Alexa publication

Keep Alexa publication configuration separate from raw HA entity data.

`alexa_publications`
- `id`
- `entity_id FK`
- `enabled`
- `display_name`
- `description`
- `display_category`
- `mapper_type`
- `control_allowed`
- `state_read_allowed`
- `policy_json`
- `alexa_endpoint_id`
- `published_at`
- `updated_at`
- `removed_at`

This separation is important.

Changing a Home Assistant friendly name MUST NOT automatically change an Alexa name if the installer previously customized it.

## 6.9 Alexa account link

`alexa_account_links`
- `id`
- `tenant_id`
- `user_id`
- `provider_subject`
- `status`
- `created_at`
- `updated_at`
- `unlinked_at`

Tokens should be stored separately and encrypted/hashed as appropriate.

## 6.10 Alexa event authorization

Store the event gateway authorization required by the supported Alexa model.

Do not invent token semantics; implement according to official Amazon flow.

## 6.11 Pairing session

`pairing_sessions`
- `id`
- `code_hash`
- `installation_nonce`
- `expires_at`
- `claimed_by_user_id`
- `claimed_tenant_id`
- `claimed_installation_id`
- `status`
- `created_at`
- `claimed_at`

Code expires in 10 minutes by default.

Pairing code must be one-time use.

## 6.12 Audit event

`audit_events`
- `id`
- `tenant_id`
- `installation_id nullable`
- `user_id nullable`
- `source`
- `event_type`
- `request_id`
- `payload_redacted_json`
- `result`
- `created_at`

---

# 7. HOME ASSISTANT INTEGRATION

Integration domain:

`ekonex_voice`

Display name:

`Ekonex Voice`

## 7.1 Ekonex Home Assistant Integration Standard

All Ekonex custom integrations for Home Assistant MUST follow a shared standard,
called the **Ekonex Home Assistant Integration Standard**.

HAOS/Home Assistant OS (historically also called Hassio) is the primary and
reference platform for development, validation, installation instructions, and
support. Other Home Assistant installation types may be supported when they do
not weaken compatibility with current official Home Assistant APIs or the HAOS
user experience.

Before starting M3, Codex MUST locate and analyze the existing Ekonex Home
Assistant components made available by Ekonex. This is a mandatory prerequisite,
not an optional refactoring activity. The analysis MUST identify reusable
conventions and compatibility requirements for:

- integration lifecycle, including setup, unload, reload, and shutdown;
- config flow, reauthentication, reconfiguration, and abort behavior;
- diagnostics and secret/data redaction;
- connection supervision, reconnect policy, backoff, and task cancellation;
- structured logging, log levels, context fields, and secret filtering;
- integration domain, class, module, service, event, and user-facing naming;
- stable `unique_id` rules and duplicate-installation prevention;
- `strings.json`, translations, placeholders, and Italian/English terminology;
- exception taxonomy, user-visible errors, retryable failures, and recovery.

Before M3 implementation begins, create a documented compatibility analysis that:

1. inventories the previous Ekonex Home Assistant components reviewed;
2. records the conventions that Ekonex Voice will reuse or normalize;
3. identifies conflicts with current official Home Assistant requirements;
4. defines the resulting Ekonex standard for the areas listed above;
5. records justified deviations in an ADR when they change architecture.

Previous Ekonex code is a consistency input, not authority over current Home
Assistant behavior. Current official Home Assistant documentation and accepted
APIs take precedence when an older component is deprecated or incompatible.

M3 MUST NOT begin until this analysis is complete and its conclusions are
reflected in the implementation plan and tests.

## 7.2 Installation

First distribution:
- custom integration in repository;
- later HACS-compatible distribution if appropriate.

Do NOT require editing `configuration.yaml`.

## 7.3 manifest.json requirements

Use current HA manifest schema.

Expected concepts:
- domain `ekonex_voice`
- name `Ekonex Voice`
- `config_flow: true`
- integration type appropriate for a cloud service
- version
- documentation
- issue tracker
- dependencies only when required

Codex must verify current accepted keys before commit.

## 7.4 Config flow

Desired setup:

### Step 1 — Start

User chooses:
`Add Integration → Ekonex Voice`

Display:
- brief explanation;
- cloud environment (hidden/default production in normal build);
- button `Collega Ekonex Voice`.

### Step 2 — Pairing

Connector requests pairing session.

Home Assistant shows:

`Codice Ekonex: ABCD-1234`

Buttons/links:
- `Apri portale Ekonex`
- `Annulla`

Also show:
`Il codice scade tra 10 minuti.`

### Step 3 — Claim

Installer logs into Ekonex portal and enters code.

Portal asks:
- existing tenant or new tenant;
- installation name.

Example:
Tenant: `Villa Rossi`
Installation: `Home`

### Step 4 — Completion

HA polls server with a pairing-session credential, not a reusable secret.

Cloud responds:
- installation_id
- connector credential/token
- tenant display name

HA stores only required credentials in ConfigEntry.

Display:
`Ekonex Voice collegato a Villa Rossi / Home`

## 7.5 Reauthentication / reconfiguration

Must support:
- expired/revoked credential;
- change cloud endpoint in development;
- reconnect;
- unlink/relink.

Do not force user to delete/reinstall integration for routine credential recovery.

## 7.6 Runtime data

Use current typed ConfigEntry runtime-data pattern.

Avoid global mutable dictionaries when current HA patterns provide a better lifecycle mechanism.

## 7.7 Unload/reload

`async_unload_entry` must:
- unsubscribe state listeners;
- close WSS cleanly;
- stop heartbeat tasks;
- cancel reconnect tasks;
- clear runtime data.

Reload must work without restarting HA.

## 7.8 Entity inventory

Connector reads HA registry/state metadata.

For each candidate entity send only useful data:

```json
{
  "entity_id": "light.cucina",
  "domain": "light",
  "state": "on",
  "friendly_name": "Luce cucina",
  "area_id": "...",
  "area_name": "Cucina",
  "device_class": null,
  "supported_features": 40,
  "available": true,
  "attributes": {
    "brightness": 180,
    "color_mode": "color_temp",
    "color_temp_kelvin": 3000
  }
}
```

Do NOT upload every raw attribute.

Create an allowlist per HA domain.

## 7.9 Entity inventory lifecycle

Full sync:
- initial connection;
- explicit refresh;
- after connector upgrade if schema version changed.

Delta sync:
- entity added;
- entity removed;
- registry metadata changed;
- relevant capability changed.

State update:
- state changed;
- relevant property changed;
- availability changed.

## 7.10 State subscription

Use Home Assistant internal event/lifecycle APIs appropriate to a custom integration.

Do not connect from the integration back into the local `/api/websocket` unless there is a concrete need.

The connector itself is already running inside HA and should use native Python APIs.

## 7.11 State debounce/coalescing

Rapid changes may occur.

Rules:
- coalesce non-critical state updates per entity for ~250–500 ms;
- do not delay command acknowledgements;
- availability changes should be prompt;
- preserve latest state;
- avoid flooding cloud.

## 7.12 Command execution

Cloud sends an abstract command.

Example:

```json
{
  "version": 1,
  "type": "command",
  "id": "2ed...",
  "installation_id": "...",
  "entity_id": "light.cucina",
  "operation": "power_on",
  "parameters": {}
}
```

Connector validates:
1. authenticated server session;
2. message schema;
3. installation_id matches local installation;
4. entity exists;
5. domain matches expected mapper;
6. requested operation allowed by connector protocol;
7. requested values within valid bounds.

Only then map to HA service:

`power_on` → `light.turn_on`

Do NOT allow cloud to send arbitrary HA service names in production protocol.

This is a crucial security rule.

Bad:
```json
{"service": "shell_command.anything"}
```

Good:
```json
{"operation": "power_on", "entity_id": "light.cucina"}
```

The connector maps allowed operations locally.

## 7.13 Allowed operation registry

Example:

```python
ALLOWED_OPERATIONS = {
    "light": {
        "power_on",
        "power_off",
        "set_brightness",
        "set_color_temperature",
        "set_color"
    },
    "switch": {
        "power_on",
        "power_off"
    },
    "cover": {
        "open",
        "close",
        "stop",
        "set_position"
    },
    "climate": {
        "set_target_temperature",
        "set_hvac_mode"
    },
    "scene": {
        "activate"
    }
}
```

Each operation has strict Pydantic-style validation or equivalent.

## 7.14 Diagnostics

Provide downloadable HA diagnostics.

Include:
- connector version;
- cloud hostname;
- installation public ID;
- connection state;
- last connection time;
- reconnect count;
- queued state count;
- entity counts by domain;
- recent error codes.

Redact:
- connector token;
- secrets;
- account tokens;
- auth headers;
- precise sensitive data not needed for support.

---

# 8. CONNECTOR ↔ CLOUD PROTOCOL

Protocol name:
`EVCP` — Ekonex Voice Connector Protocol

Version:
`1`

Transport:
WSS + TLS.

Endpoint:
`/connector/v1/ws`

## 8.1 Envelope

```json
{
  "version": 1,
  "type": "hello",
  "id": "uuid",
  "timestamp": "2026-08-17T12:00:00Z",
  "payload": {}
}
```

## 8.2 Message types

Client → cloud:
- `hello`
- `heartbeat`
- `inventory_full`
- `inventory_delta`
- `state_update`
- `command_result`
- `diagnostic_event`

Cloud → client:
- `hello_ack`
- `heartbeat_ack`
- `command`
- `inventory_refresh_request`
- `policy_refresh`
- `disconnect`

## 8.3 Hello

Connector:

```json
{
  "version": 1,
  "type": "hello",
  "id": "...",
  "payload": {
    "installation_id": "...",
    "connector_version": "0.1.0",
    "ha_version": "2026.x",
    "protocol_versions": [1]
  }
}
```

Authentication should be done with secure connection credentials in headers or a clearly defined auth handshake.

Do not put long-lived secret values in normal message payloads.

## 8.4 Command result

```json
{
  "version": 1,
  "type": "command_result",
  "id": "...",
  "payload": {
    "command_id": "...",
    "status": "success",
    "entity_id": "light.cucina",
    "state": {
      "power": "ON"
    }
  }
}
```

Failure:

```json
{
  "status": "error",
  "error_code": "ENTITY_UNAVAILABLE",
  "message": "Entity is unavailable"
}
```

Do not expose Python stack traces to cloud consumers.

## 8.5 Error codes

Stable protocol codes:

- `AUTH_FAILED`
- `INSTALLATION_REVOKED`
- `UNSUPPORTED_PROTOCOL`
- `INVALID_MESSAGE`
- `ENTITY_NOT_FOUND`
- `ENTITY_UNAVAILABLE`
- `OPERATION_NOT_SUPPORTED`
- `INVALID_PARAMETER`
- `SERVICE_CALL_FAILED`
- `COMMAND_TIMEOUT`
- `INTERNAL_ERROR`

## 8.6 Heartbeat

Default every 30 s.

Cloud considers connection degraded after missing expected heartbeats and offline after a configurable threshold.

## 8.7 Reconnection

Jittered exponential backoff:

1 s
2 s
5 s
10 s
30 s
60 s max

Add random jitter to avoid reconnect storms after cloud restart.

---

# 9. ALEXA ADAPTER

Ekonex Voice must implement the current Amazon Smart Home integration model.

Do NOT use a Custom Skill invocation model for normal device commands.

Desired customer speech:

`Alexa, accendi luce cucina`

NOT:

`Alexa, chiedi a Ekonex di accendere luce cucina`

## 9.1 Alexa ingress

Cloud exposes one Alexa directive endpoint.

Conceptual endpoint:

`POST /alexa/v1/directive`

Actual deployment shape must comply with current Amazon requirements.

## 9.2 Directive processing pipeline

```text
Alexa directive
      ↓
validate envelope
      ↓
validate authorization / account
      ↓
resolve tenant
      ↓
resolve endpoint
      ↓
check publication policy
      ↓
map Alexa directive → internal operation
      ↓
find connected installation
      ↓
send EVCP command
      ↓
receive command_result
      ↓
map result → Alexa response
```

## 9.3 No direct entity IDs from Alexa

Alexa endpoint ID maps server-side to publication/entity.

Never trust:
- endpoint names;
- user speech text;
- arbitrary entity IDs from directive metadata.

## 9.4 Stable endpoint IDs

Alexa endpoint ID must remain stable across:
- HA restart;
- friendly-name change;
- Ekonex display-name change;
- connector reconnect.

Recommended opaque form:

`ev1_<random-or-derived-stable-id>`

Do not expose tenant UUID/entity_id if unnecessary.

## 9.5 Alexa Discovery

For linked account, return only:
- tenant-owned;
- enabled;
- supported;
- policy-approved endpoints.

Never return another tenant's endpoint.

Discovery data includes:
- endpointId;
- manufacturerName `Ekonex`;
- friendlyName;
- description;
- displayCategories;
- capabilities.

Capabilities are generated by mapper classes.

## 9.6 Mapper architecture

Create domain-specific mappers:

```text
AlexaMapper
├── LightAlexaMapper
├── SwitchAlexaMapper
├── CoverAlexaMapper
├── ClimateAlexaMapper
├── FanAlexaMapper
├── SceneAlexaMapper
└── LockAlexaMapper (later / security review)
```

Interface:

```python
class AlexaMapper(Protocol):
    def supports(entity, publication) -> bool: ...
    def build_discovery(entity, publication) -> dict: ...
    def directive_to_operation(directive, entity, publication) -> InternalCommand: ...
    def state_to_properties(entity) -> list[AlexaProperty]: ...
```

No giant `if/elif` handler with all domains.

## 9.7 V1 supported domains

MVP:
- `light`
- `switch`

Phase 2:
- `cover`
- `scene`
- `fan`

Phase 3:
- `climate`

Security-sensitive later:
- `lock`
- garage/gate/opening devices
- alarm control

Do NOT implement sensitive unlock/disarm/open actions until Amazon requirements and Ekonex policy are explicitly reviewed.

## 9.8 Light

Potential capabilities depending on HA support:
- PowerController
- BrightnessController
- ColorController
- ColorTemperatureController

Do not advertise capability unless underlying HA entity supports it.

## 9.9 Switch

- PowerController

Potential future semantic categorization:
- smart plug
- other switchable appliance

## 9.10 Cover

Map to current Alexa-supported cover/blind capability semantics.

Do NOT assume percentage semantics without checking current Amazon interface requirements.

HA:
- open
- close
- stop
- set_position if supported

## 9.11 Climate

Potential:
- target temperature;
- current temperature;
- HVAC mode.

Units and supported ranges must be validated.

## 9.12 Scene

- activation only in initial implementation.

## 9.13 Generic sensors

Do NOT expose every HA sensor.

Only expose a sensor if:
- Amazon has an appropriate official capability/interface;
- units and state semantics can be represented correctly;
- it makes sense to Alexa.

Do not fake photovoltaic power as temperature, switch, etc.

PV/energy Q&A belongs to a future Ekonex conversational feature, not to incorrect Smart Home capability mapping.

---

# 10. STATE REPORTING

Alexa state must stay synchronized with HA.

## 10.1 After commands

After successful command:
- read/confirm resulting HA state where practical;
- return Alexa response using actual state.

Do not blindly echo requested state when HA rejected or transformed it.

## 10.2 Proactive state changes

When HA state changes independently:
- physical button;
- HA dashboard;
- automation;
- schedule;
- another integration;

Connector sends state_update → cloud.

Cloud maps it to supported Alexa proactive state event (`ChangeReport` where applicable).

## 10.3 Event deduplication

Cloud must prevent duplicate ChangeReports caused by:
- command response plus immediate HA state event;
- reconnect replay;
- repeated identical state.

Track:
- last Alexa-reported property value;
- event timestamp;
- source where useful.

Do not suppress legitimate changes.

## 10.4 Discovery updates

When installer:
- enables publication;
- disables publication;
- changes Alexa name;
- entity is removed;
- capability materially changes;

Cloud should use current supported Alexa mechanisms to add/update/delete endpoints proactively where applicable.

Avoid forcing customer to manually "discover devices" for routine changes if current API supports proactive update.

---

# 11. ACCOUNT LINKING / OAUTH

Ekonex Cloud is the account system used by Alexa account linking.

Use OAuth 2.0 Authorization Code flow compliant with current Amazon account linking requirements.

Endpoints conceptually:

- `GET /oauth/authorize`
- `POST /oauth/token`
- optional supported revoke/logout flows

## 11.1 Authorization flow

1. Customer enables Ekonex Voice in Alexa app.
2. Amazon opens Ekonex authorization page.
3. Customer logs into Ekonex.
4. If user has one tenant, select automatically.
5. If multiple allowed homes/tenants, show selector.
6. User grants connection.
7. Ekonex redirects with authorization code.
8. Amazon exchanges code for token.
9. Alexa discovery occurs.

## 11.2 OAuth security

Mandatory:
- high-entropy authorization codes;
- short code TTL;
- one-time code usage;
- secure token generation;
- refresh-token rotation where supported;
- token revocation;
- HTTPS only;
- CSRF/state validation;
- redirect URI strict allowlist;
- no wildcard redirects;
- no tokens in logs;
- secrets stored in secret manager/env, never Git.

## 11.3 Tenant binding

Account link binds Alexa identity to the chosen Ekonex tenant.

Every Alexa directive must resolve:

token → account link → tenant → endpoint → installation.

Never accept tenant ID from the caller as authority.

---

# 12. ADMIN PORTAL

Name:
`Ekonex Voice Manager`

Primary users:
- Ekonex administrator;
- installer;
- support.

## 12.1 Dashboard

Show:
- total tenants;
- online installations;
- offline installations;
- published Alexa endpoints;
- connector versions;
- recent command failures.

## 12.2 Customer list

Columns:
- customer name;
- installation count;
- Alexa linked yes/no;
- endpoints;
- status;
- last activity.

## 12.3 Installation page

Header:
- tenant;
- installation name;
- Online / Offline;
- Home Assistant version;
- connector version;
- last seen;
- reconnect status.

Entity table:

| Publish | HA entity | HA name | Area | HA type | Alexa name | Alexa type | Control | State |
|---|---|---|---|---|---|---|---|---|

Functions:
- search;
- filter by domain;
- filter by area;
- filter supported/unsupported;
- bulk enable;
- bulk disable;
- edit Alexa name;
- choose allowed mapper if ambiguous;
- control permission;
- state-read permission;
- test action;
- refresh inventory.

## 12.4 Safe defaults

On first HA sync:
`enabled = false`

Installer explicitly publishes desired endpoints.

Optional later:
safe automatic defaults for common lights.

For MVP, manual enable is safer.

## 12.5 Unsupported entity display

Show entity but mark:
`Non supportato da Alexa`

Do NOT silently hide all unsupported HA entities from installer; support visibility helps diagnostics.

---

# 13. CONTROL POLICY

Each publication has policy:

```json
{
  "control_allowed": true,
  "state_read_allowed": true,
  "operations": [
    "power_on",
    "power_off"
  ]
}
```

Cloud checks policy before sending connector command.

Connector also independently checks protocol/domain operation.

Defense in depth.

---

# 14. SECURITY-SENSITIVE DEVICES

Treat separately:
- locks;
- gates;
- garage doors;
- alarm systems;
- security relays;
- potentially hazardous loads.

Do not treat a gate as a generic `switch` just to make it work.

For sensitive operations:
1. check current Alexa device API requirements;
2. check voice confirmation/PIN requirements if applicable;
3. define Ekonex policy;
4. require explicit installer opt-in;
5. audit every operation;
6. consider disabling sensitive operation by default.

MVP must NOT include alarm disarm or door unlock.

---

# 15. CLOUD API

Base:
`/api/v1`

## 15.1 Health

`GET /health`

Response:
```json
{
  "status": "ok",
  "version": "..."
}
```

Separate readiness/liveness in production.

## 15.2 Session auth

- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`
- `GET /api/v1/me`

## 15.3 Pairing

Connector:
- `POST /api/v1/pairing`

Portal:
- `POST /api/v1/pairing/claim`

Connector:
- `GET /api/v1/pairing/{session_id}`

Use non-secret public session IDs plus secret polling credential if needed.

Do NOT rely only on a short human code as authentication.

## 15.4 Tenants

- `GET /api/v1/tenants`
- `POST /api/v1/tenants`
- `GET /api/v1/tenants/{id}`
- `PATCH /api/v1/tenants/{id}`

## 15.5 Installations

- `GET /api/v1/installations`
- `GET /api/v1/installations/{id}`
- `PATCH /api/v1/installations/{id}`
- `POST /api/v1/installations/{id}/revoke`
- `POST /api/v1/installations/{id}/rotate-credential`
- `POST /api/v1/installations/{id}/request-inventory-refresh`

## 15.6 Entities

- `GET /api/v1/installations/{id}/entities`
- `GET /api/v1/entities/{id}`

Raw HA entity records should normally be mutated by connector sync, not manually by portal.

## 15.7 Alexa publication

- `GET /api/v1/entities/{id}/alexa-publication`
- `PUT /api/v1/entities/{id}/alexa-publication`
- `DELETE /api/v1/entities/{id}/alexa-publication`

Example:

```json
{
  "enabled": true,
  "display_name": "Luce cucina",
  "mapper_type": "light",
  "control_allowed": true,
  "state_read_allowed": true
}
```

## 15.8 Test command

Installer-only:
`POST /api/v1/entities/{id}/test-command`

Allow only safe domain-specific operations.

Never expose arbitrary HA service-call UI in customer portal.

---

# 16. AUTHORIZATION

Every backend route must use centralized authorization.

Examples:

Installer may:
- view assigned tenants;
- manage publications;
- diagnose connector.

Customer admin may:
- view own tenant;
- possibly rename Alexa endpoint;
- cannot access another tenant.

Support readonly:
- diagnostics;
- no device command unless elevated flow exists.

Avoid sprinkling manual `if user.role == ...` everywhere.

Implement policy/service layer.

---

# 17. TENANT ISOLATION — NON-NEGOTIABLE

Every tenant-owned record must be queried through tenant scope.

Bad:

```python
session.get(Entity, entity_id)
```

then later checking tenant if developer remembers.

Preferred:
repository/service methods require tenant context:

```python
entity_repo.get_for_tenant(
    tenant_id=current_tenant.id,
    entity_id=entity_id
)
```

Tests MUST attempt cross-tenant attacks.

Examples:
- tenant A requests entity UUID of tenant B;
- tenant A attempts publication edit on B;
- Alexa token for A sends endpoint ID belonging to B;
- connector for installation A sends B installation_id.

Expected:
deny without leaking existence/details.

---

# 18. SECRET MANAGEMENT

Never commit:
- database password;
- Redis password;
- OAuth client secret;
- Amazon credentials;
- connector secrets;
- signing keys;
- production URLs containing credentials.

Development:
`.env`

Repository:
`.env.example`

Production:
secret manager/env injection.

GitHub Actions:
GitHub encrypted secrets/OIDC where appropriate.

Add secret scanning.

---

# 19. LOGGING / OBSERVABILITY

Use structured JSON logs.

Required fields where applicable:
- timestamp;
- level;
- service;
- environment;
- request_id;
- tenant_id;
- installation_id;
- connector_session_id;
- alexa_message_id;
- endpoint_id;
- operation;
- latency_ms;
- result;
- error_code.

Never log:
- passwords;
- bearer tokens;
- refresh tokens;
- connector credentials;
- OAuth authorization codes.

## 19.1 Metrics

Future/production:
- connected installations;
- connection churn;
- command latency p50/p95/p99;
- Alexa command success rate;
- HA command failure rate;
- ChangeReport delivery failure;
- OAuth errors;
- pairing completion rate.

---

# 20. COMMAND LIFECYCLE

Example: Turn on kitchen light.

1. Alexa sends PowerController TurnOn.
2. Alexa Adapter authenticates account.
3. Resolve tenant.
4. Resolve endpoint publication.
5. Verify enabled and `control_allowed`.
6. Verify entity domain/light capability.
7. Resolve installation.
8. Check installation session online.
9. Create command with unique ID.
10. Send through WSS.
11. Connector validates command.
12. Connector calls `light.turn_on`.
13. Connector reads resulting HA state.
14. Connector sends `command_result`.
15. Cloud sends correct Alexa response.
16. HA state change event also reaches cloud.
17. Dedup logic prevents unnecessary duplicate proactive event.
18. Audit event stored.

Target internal cloud-to-connector command timeout:
~8 seconds maximum for MVP, but Alexa response constraints must be checked and honored.

Do NOT assume the Alexa timeout equals 8 s; adapter must follow Amazon's current requirement.

---

# 21. OFFLINE BEHAVIOR

## HA offline

- connector session missing;
- endpoints remain registered in Alexa;
- command returns appropriate unavailable/error response;
- admin shows installation offline;
- endpoint is NOT automatically deleted.

## Entity unavailable

- command rejected with `ENTITY_UNAVAILABLE`;
- availability propagated to Alexa if interface supports it.

## Cloud restart

- connector reconnects automatically with jitter;
- state/inventory consistency restored;
- no manual customer action.

## HA restart

- integration loads;
- connector reconnects;
- session reauthenticates;
- inventory reconciliation runs;
- published endpoints remain stable.

---

# 22. DATA CONSISTENCY

Cloud is NOT source of truth for physical device state.

Home Assistant is source of truth.

Cloud stores last-known state for:
- Alexa state reporting;
- UI;
- diagnostics.

Each state record should have:
- value;
- HA timestamp;
- received timestamp;
- availability.

After reconnection, connector should reconcile relevant current state.

---

# 23. ALEXA DISPLAY NAME POLICY

Default name when first published:
1. installer's explicit choice;
2. otherwise HA friendly_name.

Once publication exists:
- HA friendly_name changes must NOT automatically overwrite explicit Alexa display name.

Provide:
`Reset Alexa name from Home Assistant`

Name validation:
- length;
- empty values;
- duplicate warning;
- unsupported characters per current Alexa requirements.

Duplicates within tenant should trigger installer warning.

---

# 24. PAIRING SECURITY

Human code:
`ABCD-1234`

Requirements:
- random;
- short lifetime;
- one time;
- rate-limited claim;
- not sufficient alone for final connector authentication.

Pairing flow must defend against:
- brute force;
- session fixation;
- replay;
- accidental tenant claim;
- leaked code.

After claim, issue a new strong connector credential.

The human pairing code is never the long-lived connector secret.

---

# 25. DATABASE / MIGRATIONS

Use Alembic.

Rules:
- every schema change has migration;
- migrations reviewed;
- no destructive migration without explicit migration plan;
- PostgreSQL-specific features allowed if justified;
- timestamps UTC;
- UUID primary keys preferred for externally referenced resources;
- indexes on tenant/installation/entity lookup paths.

Likely indexes:
- installations(tenant_id)
- entities(installation_id, ha_entity_id)
- alexa_publications(alexa_endpoint_id)
- audit_events(tenant_id, created_at)
- pairing_sessions(expires_at)

---

# 26. REDIS USAGE

Redis can be used for:
- connected connector session registry;
- command request/response correlation;
- short TTL pairing/poll state;
- distributed locks;
- rate limiting;
- pub/sub between horizontally scaled workers.

Do NOT make Redis the only durable store for tenant/entity configuration.

PostgreSQL is durable source for application configuration.

---

# 27. HORIZONTAL SCALING

Design cloud so:
- connector WSS may land on worker A;
- Alexa directive may land on worker B.

Therefore worker B must be able to route command to the connector session on worker A.

Use Redis-backed session routing/pub-sub or another explicit mechanism.

Do not implement a global in-process dict as the production routing architecture.

For local MVP, in-process can exist only behind an abstraction with Redis implementation planned/available.

---

# 28. API IDEMPOTENCY

Alexa/directive and cloud retries may happen.

Command layer should use:
- unique message/directive ID;
- short-lived idempotency store;
- avoid executing same destructive operation twice when retry detected.

Especially important later for:
- scenes;
- covers;
- locks/gates.

---

# 29. TEST STRATEGY

## 29.1 Unit tests

Cloud:
- models;
- authorization;
- tenant scoping;
- mapper logic;
- directive parsing;
- response mapping;
- pairing expiry;
- credential hashing.

HA:
- config flow;
- reconnect;
- entity allowlist;
- command validation;
- command mapping;
- diagnostics redaction.

## 29.2 Alexa fixtures

Keep official-style JSON fixtures for:
- Discovery request/response;
- TurnOn;
- TurnOff;
- ReportState if used;
- endpoint unavailable;
- invalid endpoint;
- unsupported directive;
- authorization failure;
- ChangeReport.

Snapshot-test JSON where useful.

## 29.3 Cross-tenant security tests

Mandatory.

Test at least:
- read entity;
- modify publication;
- command endpoint;
- connector authentication;
- pairing claim.

## 29.4 Integration tests

Simulated connector:
- connects;
- inventory sync;
- receives command;
- result returns.

## 29.5 End-to-end test

MVP scenario:

HA fixture entity:
`light.cucina`

Flow:
1. connector pairs;
2. inventory arrives;
3. publication enabled;
4. discovery returns endpoint;
5. Alexa TurnOn fixture sent;
6. connector receives `power_on`;
7. simulated HA state becomes on;
8. Alexa response reports ON.

---

# 30. CI/CD

GitHub Actions PR checks:

- ruff check;
- ruff format --check;
- mypy;
- pytest;
- frontend lint/test;
- migration sanity;
- secret scan;
- dependency/security scan where practical.

No merge when required checks fail.

Use branch protection later.

---

# 31. DEVELOPMENT RULES FOR CODEX

Codex MUST:

1. read `docs/SPEC_V1.md` before coding;
2. inspect existing repo before creating duplicate architecture;
3. work milestone by milestone;
4. keep PRs small;
5. write tests with implementation;
6. run relevant tests before concluding;
7. report exactly what changed;
8. list commands executed;
9. document deviations from spec;
10. create ADR for architectural deviation;
11. use official Amazon docs for Alexa interfaces;
12. use official HA developer docs for integration lifecycle;
13. not invent undocumented payloads;
14. not weaken tenant isolation for convenience;
15. not implement arbitrary remote HA service execution;
16. never commit secrets;
17. avoid unnecessary dependencies;
18. prefer typed code;
19. keep API schemas versioned;
20. preserve backward compatibility of EVCP within a major protocol version.
21. before M3, analyze previous Ekonex Home Assistant components and document
    the Ekonex Home Assistant Integration Standard required by section 7.1.

---

# 32. ARCHITECTURAL DECISIONS

Create ADRs.

Initial ADRs:

`ADR-0001-monorepo.md`
- one monorepo for cloud/adapter/HA/shared.

`ADR-0002-outbound-wss.md`
- HA initiates outbound WSS, no inbound customer ports.

`ADR-0003-multitenancy.md`
- tenant as mandatory isolation boundary.

`ADR-0004-safe-command-protocol.md`
- cloud sends abstract allowlisted operations, not arbitrary HA services.

`ADR-0005-home-assistant-source-of-truth.md`
- physical state remains HA authority.

`ADR-0006-one-alexa-integration.md`
- one Ekonex Alexa integration for all customers through OAuth account linking.

---

# 33. MILESTONES

## M0 — Repository bootstrap

Deliver:
- monorepo;
- FastAPI;
- PostgreSQL;
- Redis;
- SQLAlchemy;
- Alembic;
- Docker Compose;
- health endpoint;
- config from env;
- pytest;
- ruff;
- mypy;
- GitHub Actions;
- README;
- ADR skeleton.

NO Alexa implementation.
NO Home Assistant connector yet.

Definition:
`docker compose up` starts dependencies and API.
`GET /health` succeeds.
CI green.

## M1 — Core multi-tenant backend

Deliver:
- users;
- tenants;
- membership;
- installations;
- entities;
- publications;
- migrations;
- tenant-scoped repositories;
- auth basics;
- cross-tenant tests.

No Alexa control yet.

## M2 — Pairing

Deliver:
- create pairing session;
- claim flow;
- installation credential issuance;
- credential hashing;
- expiration;
- replay protection;
- tests.

## M3 — Home Assistant connector foundation

Mandatory entry criterion:
- complete the pre-M3 analysis of previous Ekonex Home Assistant components;
- document the Ekonex Home Assistant Integration Standard for lifecycle, config
  flow, diagnostics, reconnect, logging, naming, `unique_id`, translations, and
  error handling;
- confirm HAOS/Home Assistant OS (Hassio) as the primary validation platform;
- resolve conflicts with current official Home Assistant requirements before
  implementation, recording architectural deviations in an ADR.

M3 implementation MUST NOT start until this entry criterion is satisfied.

Deliver:
- manifest;
- config flow;
- pairing UI;
- config entry;
- outbound WSS;
- hello/auth;
- heartbeat;
- reconnect;
- unload/reload;
- diagnostics.

No Alexa yet.

## M4 — HA entity inventory

Deliver:
- `light`;
- `switch`;
- inventory full sync;
- state updates;
- availability;
- cloud persistence;
- admin list.

## M5 — Safe command path

Deliver:
- internal command schema;
- light power on/off;
- switch power on/off;
- connector validation;
- cloud routing;
- result correlation;
- audit;
- tests.

At this milestone, admin "test command" should work end-to-end without Alexa.

## M6 — Alexa account linking

Deliver:
- OAuth authorization;
- token endpoint;
- tenant binding;
- Alexa account-link records;
- tests.

## M7 — Alexa Discovery MVP

Deliver only:
- lights;
- switches;
- stable endpoint IDs;
- Discovery;
- supported capabilities;
- tenant isolation.

## M8 — Alexa control MVP

Deliver:
- TurnOn;
- TurnOff;
- response mapping;
- unavailable/error handling;
- idempotency;
- metrics/logging.

MVP "wow moment":

`Alexa, accendi luce cucina`

must turn on HA `light.cucina`.

## M9 — State synchronization

Deliver:
- HA state changes;
- cloud latest state;
- proactive ChangeReport where supported;
- dedup;
- Alexa state consistency.

## M10 — Dynamic endpoint management

Deliver:
- enable publication → add/update endpoint;
- disable publication → remove endpoint;
- rename;
- capability change reconciliation.

## M11 — Covers and scenes

Add:
- cover open/close/stop/position according to supported Alexa API;
- scene activation.

## M12 — Fan and climate

Add after validation against current Amazon semantics.

## M13 — Security-sensitive devices

Separate security review before:
- locks;
- gates;
- garage;
- alarms.

Never fold these casually into generic switches.

---

# 34. MVP DEFINITION OF DONE

MVP is complete only if all are true:

1. Fresh HA can install Ekonex Voice from UI.
2. Pairing code is generated.
3. Installer claims it in Ekonex portal.
4. HA establishes outbound WSS.
5. Cloud shows HA Online.
6. Cloud receives `light` and `switch`.
7. Installer enables `light.cucina`.
8. Alexa account links to correct tenant.
9. Alexa Discovery sees only that tenant's published endpoints.
10. Alexa discovers `Luce cucina`.
11. "Alexa, accendi luce cucina" turns it on.
12. "Alexa, spegni luce cucina" turns it off.
13. Alexa receives correct resulting state.
14. Manual HA state changes are eventually reflected in Alexa via supported reporting.
15. HA restart reconnects automatically.
16. Cloud restart permits automatic reconnect.
17. Router has no port forwarding.
18. No secret appears in logs/diagnostics.
19. Cross-tenant security tests pass.
20. Connector cannot execute arbitrary HA services.
21. CI is green.

---

# 35. V1 NON-GOALS

Do NOT build yet:
- generic ChatGPT/LLM assistant;
- custom Alexa conversational Q&A;
- arbitrary HA service executor;
- alarm disarm;
- door unlock;
- video streaming;
- energy analytics;
- billing;
- dealer marketplace;
- native mobile app;
- voice hardware;
- Google Home;
- Apple Home;
- Matter bridge.

These can be future products/features.

---

# 36. FUTURE ROADMAP

Possible later additions:

## Ekonex Voice Assist

Conversational queries:
- "Quanto sta producendo il fotovoltaico?"
- "Quanta batteria ho?"
- "Perché il riscaldamento è spento?"

This should be a separate conversation layer, not fake Alexa Smart Home capabilities.

## Ekonex Voice Satellite

Dedicated microphone/speaker:
- local HA Assist;
- Ekonex branding;
- optional cloud intelligence.

## Google Home / other ecosystems

Reuse:
- tenants;
- installations;
- entity registry;
- connector;
- policies.

Add ecosystem adapters.

The architecture must keep Alexa-specific code isolated so this is feasible.

---

# 37. FIRST CODEX PROMPT — M0

You are the lead developer for the repository `ekonex-voice`.

Before making any change, read `docs/SPEC_V1.md` in full.

Your task is ONLY Milestone M0.

Create a production-minded but minimal monorepo bootstrap with:

Backend:
- Python 3.13
- FastAPI
- Pydantic v2
- SQLAlchemy 2.x
- Alembic
- PostgreSQL
- Redis

Development:
- Docker Compose
- `.env.example`
- pytest
- pytest-asyncio
- ruff
- mypy

API:
- `GET /health`
- configuration loaded only from environment/settings
- no hardcoded production credentials

GitHub:
- CI workflow for lint, type-check and tests

Documentation:
- root README with local setup
- `docs/architecture.md`
- ADR folder
- ADR skeleton and initial architectural ADRs if appropriate

Constraints:
- DO NOT implement Alexa yet.
- DO NOT implement the Home Assistant connector yet.
- DO NOT implement OAuth yet.
- DO NOT implement business UI yet.
- DO NOT add unnecessary dependencies.
- DO NOT commit secrets.
- Prefer async-compatible architecture.
- Keep modules small and typed.
- Make future multi-tenancy possible without prematurely implementing it.

Process:
1. inspect current repository;
2. summarize existing state;
3. propose exact files to create/change;
4. implement M0;
5. run formatter/lint/type-check/tests;
6. fix failures;
7. summarize changes;
8. report commands run;
9. list any deliberate deviation from `SPEC_V1.md`.

Stop after M0.
Do not begin M1 automatically.

---

# 38. SECOND CODEX PROMPT — M1

Use only after M0 is green.

Read `docs/SPEC_V1.md`.

Implement ONLY Milestone M1: Core multi-tenant backend.

Required:
- User model
- Dealer model
- Tenant model
- TenantMembership
- Installation
- Entity
- AlexaPublication
- AuditEvent
- Alembic migrations
- repository/service layer
- centralized tenant scoping
- basic authentication scaffolding as required for tests
- unit tests
- explicit cross-tenant isolation tests

Security requirement:
No service/repository method that returns tenant-owned data may rely on the caller remembering to filter tenant later.

Do not implement Alexa directives.
Do not implement HA WSS.
Do not implement pairing unless strictly necessary for M1.

Run all checks and stop after M1.

---

# 39. THIRD CODEX PROMPT — M2 PAIRING

Read `docs/SPEC_V1.md`.

Implement ONLY pairing.

Requirements:
- connector creates pairing session;
- human code format similar to `ABCD-1234`;
- code expires;
- code is one-time;
- brute-force protection;
- portal claim associates installation to a tenant;
- claim issues a new strong connector credential;
- human code is never reused as connector credential;
- store credential safely;
- support revocation;
- audit claim;
- tests for expiration, replay and cross-tenant abuse.

Do not implement Alexa.
Do not implement entity sync yet.

Stop after M2.

---

# 40. REVIEW CHECKLIST FOR EVERY PR

Before finishing a PR, Codex must answer:

- What milestone does this PR implement?
- Did scope expand beyond milestone?
- Are secrets absent?
- Are tenant-owned queries scoped?
- Are all new endpoints authorized?
- Are Pydantic/request models strict enough?
- Are error messages safe?
- Are tests included?
- Are migrations included where needed?
- Are docs updated?
- Did all tests pass?
- Did lint pass?
- Did type checking pass?
- Does Docker/dev startup still work?
- Does this introduce an arbitrary remote-execution path?
- Does this change EVCP protocol?
- If yes, is protocol versioning handled?
- Does this change architecture?
- If yes, is ADR updated?

---

# 41. CRITICAL WARNING TO CODEX

The most dangerous shortcut in this project is to make the cloud send arbitrary Home Assistant service calls.

DO NOT implement a generic protocol like:

```json
{
  "domain": "whatever",
  "service": "whatever",
  "data": {}
}
```

for remote Alexa commands.

Ekonex Voice must expose a deliberately small, versioned operation vocabulary.

Example:

```json
{
  "operation": "power_on",
  "entity_id": "light.cucina"
}
```

The Home Assistant connector maps that operation to a known HA service.

This ensures Ekonex Cloud cannot accidentally become a general remote-code/control tunnel into customer Home Assistant installations.

---

# 42. FINAL PRODUCT EXPERIENCE

Installer workflow should eventually feel like:

Home Assistant:
`Add integration → Ekonex Voice → ABCD-1234`

Ekonex Manager:
`Add installation → enter ABCD-1234 → Villa Rossi`

Entity list:
`Luce cucina → Publish`
`Tenda sala → Publish`
`Termostato → Publish`

Customer:
`Alexa App → Ekonex Voice → Enable → Login`

Then:

`Alexa, accendi luce cucina.`

No skill invocation name.
No customer developer account.
No AWS configuration per customer.
No port forwarding.
No Home Assistant YAML.
No repeated custom setup.

That is the core Ekonex Voice product promise.
