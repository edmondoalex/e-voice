# ADR-0002: Use an outbound connector WebSocket

- Status: Accepted
- Date: 2026-08-17

## Context

Customer networks must not require inbound ports, public Home Assistant URLs, or
local TLS certificate management.

## Decision

The future Home Assistant connector will initiate an authenticated TLS WebSocket
to Ekonex Cloud.

## Consequences

Installations require no router changes. The cloud must manage connection
lifecycle, horizontal routing, reconnection, and offline behavior.

