# ADR-0007: Replace an installation's previous EVCP session

Status: Accepted

## Context

EVCP permits one active cloud connection per Home Assistant installation, but the
canonical specification did not choose how a reconnect races with a stale socket.

## Decision

The latest fully authenticated and successfully bound `hello` wins. Registration
is atomic and the prior session is closed with `4008 SESSION_REPLACED`. Cleanup is
generation-safe, so a stale session cannot unregister its replacement. Session
ownership is accessed through a registry boundary; M4 supplies an in-process
implementation and requires a distributed adapter before multi-worker deployment.

## Consequences

Restarts and network recovery converge without duplicate message consumers. An
attacker still needs the installation credential to replace a session. Horizontal
WebSocket scaling remains an explicit production-infrastructure task.
