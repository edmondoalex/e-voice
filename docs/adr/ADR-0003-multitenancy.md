# ADR-0003: Make tenant scope mandatory

- Status: Accepted
- Date: 2026-08-17

## Context

One Ekonex platform serves multiple customers, and cross-customer leakage is
unacceptable.

## Decision

Tenant is the primary isolation boundary. Future repositories and services that
handle tenant-owned records will require tenant context.

## Consequences

Interfaces require explicit tenant identity and tests must cover cross-tenant
attacks. M0 records this constraint without implementing the domain model.

