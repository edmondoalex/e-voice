# Pre-M3 task

Status: Complete; ready for review in PR #5 (2026-08-17)

Source of truth: GitHub Issue #4 — Pre-M3 — Analisi standard Home Assistant Ekonex.

Codex must read Issue #4 and `docs/SPEC_V1.md`, then perform only the documented pre-M3 analysis. Do not implement M3 or create the Home Assistant Connector yet.

Required output:
- [x] `docs/EKONEX_HA_STANDARD.md`
- [x] inventory of Ekonex HA components actually inspected, with pinned revisions
- [x] conventions to reuse / normalize / discard
- [x] lifecycle, Config Flow, reauth/reconfigure, identity, registry, diagnostics,
      reconnect, logging, availability, translations and recovery standard
- [x] minimum test requirements for the future Connector
- [x] ADR assessment: no new architectural decision emerged; no ADR added

Evidence reviewed:

- Ekonex public repositories: e-Safe Ksenia Lares 4.0, e-Safe WS,
  e_mqtt_safe, e-ThermoMind, eSunMind, e-hdl-BusPro-MQTT-addon,
  e-Therm Plus KS and ea_CustomComponents/Dahua Event Listener;
- current official Home Assistant documentation for ConfigEntry lifecycle,
  config flows, unique IDs, reauth/reconfigure, diagnostics, setup failures,
  localization and the Integration Quality Scale.

Result: [`EKONEX_HA_STANDARD.md`](EKONEX_HA_STANDARD.md).

Stop with this PR ready for review.
