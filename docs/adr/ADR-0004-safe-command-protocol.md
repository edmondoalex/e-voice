# ADR-0004: Use a safe command vocabulary

- Status: Accepted
- Date: 2026-08-17

## Context

Arbitrary Home Assistant service invocation would create an unsafe general
remote-control channel.

## Decision

The future connector protocol will expose a small, versioned set of abstract
operations mapped locally to allowlisted Home Assistant services.

## Consequences

New behavior requires an explicit protocol and mapper change. The cloud cannot
submit arbitrary domains, services, or payloads.

