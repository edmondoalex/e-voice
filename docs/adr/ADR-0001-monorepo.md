# ADR-0001: Use a monorepo

- Status: Accepted
- Date: 2026-08-17

## Context

Cloud services, shared protocol definitions, the administration UI, and the Home
Assistant integration evolve together but require clear boundaries.

## Decision

Keep the components in one repository with component-specific directories and
tests while preserving independent deployability.

## Consequences

Cross-component protocol changes can be reviewed atomically. CI must remain
targeted as the repository grows, and shared code must not erase boundaries.

