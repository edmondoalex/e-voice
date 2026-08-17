# ADR-0006: Use one Alexa integration

- Status: Accepted
- Date: 2026-08-17

## Context

Per-customer Alexa skills and AWS infrastructure would make onboarding and
operations impractical.

## Decision

Use one future Ekonex Alexa Smart Home integration. Account linking will resolve
the user and tenant before discovery or control.

## Consequences

Identity, authorization, and endpoint lookup must be rigorously tenant-scoped.
Customers will not need developer accounts or custom AWS deployments.

