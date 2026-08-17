# ADR-0005: Keep Home Assistant as state authority

- Status: Accepted
- Date: 2026-08-17

## Context

Home Assistant owns physical integrations, automation, and device state.

## Decision

Home Assistant remains authoritative for physical state. The future cloud layer
will store only last-known state for responses, UI, and diagnostics.

## Consequences

Cloud state must be reconciled after reconnects and cannot override local
automation. Local operation continues during cloud outages.

