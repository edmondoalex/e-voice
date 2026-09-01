# Current work status

Last updated: 2026-08-31 (Europe/Rome)

## Conversational AI local test (2026-09-01)

- Development continues on `agent/conversation-core-demo`; the withdrawn PlaybackController
  removal is reverted on this branch and must not be deployed.
- A channel-independent, read-only conversation core now answers a bounded Italian demo for
  photovoltaic power, ACS temperature, batteries, and room temperature.
- The core returns evidence, detects ambiguity/unavailable/stale data, and cannot execute commands.
- `scripts/demo_conversation.py` provides an offline interactive test; the focused suite contains
  eight passing tests. Alexa production and the skill under certification are untouched.
- The read-only application service can now build conversational snapshots from persisted,
  installation-scoped entities. It rejects cross-tenant installation IDs and omits deleted
  entities. Sensor synchronization is prepared to include bounded `unit_of_measurement` and
  `state_class` metadata. This work remains local and undeployed.
- The installation portal now groups entities into collapsed domain panels (sensors, lights,
  covers, climate, and other types) with per-group counts. Opening a panel reveals its existing
  entity table and controls; all groups start compact. The complete 34-test admin-console suite
  passes. This user-interface change is local and does not alter Alexa Discovery.

## Alexa certification and support case

- The Ekonex Voice Smart Home skill is under Amazon certification.
- Amazon Developer Support case: `21738300901`.
- Amazon traced the Italian utterance tested at 2026-08-27 16:22 UTC. The endpoint was
  resolved correctly, but interpretation selected a value-setting path and emitted no directive.
- Amazon withdrew its earlier claims about whether Discovery semantics were present in or read by
  its device registry.
- The Developer Advocate requested removal of `Alexa.PlaybackController` from blind endpoints,
  followed by rediscovery with only one blind enabled and this exact utterance:
  `Alexa, apri le tapparelle` (no device name).
- The test result must include the exact UTC timestamp, Alexa's spoken response, and whether a
  directive reached the backend.
- We asked Amazon whether the capability change may be deployed while certification is in progress
  or must wait until review completes. Amazon acknowledged receipt; no substantive reply has
  arrived yet.
- Do not deploy, merge to `main`, or resynchronize the production Alexa endpoints until Amazon
  answers. Production and the certification target remain unchanged.

### Prepared correction

- Branch: `agent/remove-cover-playback-controller`
- Commit: `1c275c1` (`fix(alexa): remove playback controller from covers`)
- The branch removes `Alexa.PlaybackController` from cover Discovery while retaining legacy
  directive handling during propagation.
- Cover stop remains available through `Alexa.ModeController` where supported.
- Four focused tests pass; Ruff lint and formatting checks pass.
- The complete local suite cannot run on Windows because the Home Assistant pytest plugin imports
  the Unix-only `fcntl` module. CI on Linux remains the full verification gate.

## Wear OS direction

- Alexa remains the home voice channel, but the smartwatch integration should be a direct Wear OS
  application so it does not depend on Alexa Smart Home interpretation or certification.
- Initial Ekonex Voice watch experience:
  - launch the app or a Tile;
  - tap a microphone button;
  - use Wear OS speech recognition;
  - send recognized text to the authenticated Ekonex Voice backend;
  - resolve and execute the command through the existing Home Assistant Connector/EVCP path;
  - show a result and provide haptic confirmation;
  - offer favorite-device and scene shortcuts.
- A normal Wear OS app cannot register a general system-wide `Hey Ekonex` wake phrase. The user
  launches the app, Tile, or shortcut before speaking.

### Test hardware decision

- Preferred economical development device: Xiaomi Watch 2, ASIN `B0CTMTXNF9`.
- Confirmed features relevant to the project: Google Wear OS, Google Play, microphone, Wi-Fi,
  Snapdragon W5+ Gen 1, 2 GB RAM, and 32 GB storage.
- The discussed Amazon offer was approximately EUR 99.99, new, sold and fulfilled by Amazon. Check
  condition, seller, color, and current price again before purchase.
- Xiaomi Watch 5 is the stronger but more expensive alternative, with longer battery life and newer
  gesture support.
- Avoid Redmi Watch and Xiaomi Watch S-series models for this project because they use Xiaomi's
  proprietary platform rather than standard Wear OS app distribution.

### Installation and distribution

- Development does not require publishing the app.
- Install development APKs directly with ADB over Wi-Fi from a PC, or manually from an Android
  phone using an ADB-wireless installer. Developer options must be enabled on the watch.
- A phone companion app cannot silently install the watch APK; Android prevents that for security.
- If later distributed through Google Play, the app can remain free to users. A Play Console
  developer account currently has a one-time USD 25 registration fee.
- EA SAS should use an Organization developer account, which requires organization verification
  and a D-U-N-S number.
- Play Store publication is deferred until distribution to customers and automatic updates become
  necessary.

## Next actions

1. Wait for Amazon's answer on case `21738300901` without changing production Alexa Discovery.
2. When Amazon replies, decide whether to deploy the prepared branch during or after certification.
3. After an authorized deploy, rediscover and run the exact requested Italian utterance test.
4. When the Xiaomi Watch 2 is available, enable wireless debugging and install the first private
   Ekonex Voice Wear OS prototype without using the Play Store.

## Conversational AI design

- Detailed product examples and the proposed differentiated `Ekonex Turbo` direction are recorded
  in `docs/EKONEX_AI_CONVERSATION_DEMO.md`.
- The first conversational implementation should target Wear OS, where Ekonex receives recognized
  text directly. Alexa conversational support should be a later Multi-Capability version and must
  not modify the skill currently under certification.
- Selected Home Assistant sensor entities already synchronize their state to the cloud, but sensor
  units and other safe measurement metadata must be added before trustworthy answers about PV
  production, ACS temperature, batteries, energy, and environment.
- Product direction: evidence-backed answers, energy copilot, observed command outcomes,
  installation diagnostics, explicit visibility scope, personal aliases, and installer-authorized
  support. AI explains and resolves language; deterministic code performs calculations, validates
  commands, and enforces safety.
- Long-term Alexa direction: map approved Home Assistant triggers and real state changes to official
  Alexa sensor/security interfaces and proactive `ChangeReport` events. Customer-configured Alexa
  Routines may announce selected events. Alexa must not be treated as an unrestricted text-to-speech
  endpoint. Gate/door feedback must distinguish command acceptance from a physically observed
  sensor result; alarms require deterministic source mappings and multiple delivery channels.
