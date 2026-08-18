# EVCP v1 transport contract (M4)

M4 implements only the authenticated connection substrate described here. Entity
inventory/state synchronization, command execution and provider traffic are not
part of this contract.

The Connector opens `WSS /connector/v1/ws` with `Authorization: Bearer <connector
credential>`. The credential is never placed in an EVCP payload or log. The cloud
validates that it and its installation are active before accepting the socket.

Every JSON envelope contains exactly `version`, `type`, `id`, `timestamp` and
`payload`. Version is `1`, IDs are UUIDs and timestamps are timezone-aware ISO 8601.
The maximum decoded text message is 65,536 bytes. Binary, malformed, oversized,
unknown and out-of-order messages fail closed.

The first client message is `hello`, carrying `installation_id`,
`connector_version`, `ha_version` and `protocol_versions: [1]`. The cloud returns a
correlated `hello_ack` with `installation_id`, a new `session_id`, and
`heartbeat_interval_seconds`. Heartbeats carry that session ID and receive a
correlated `heartbeat_ack`. The handshake timeout is 10 seconds; the liveness
timeout is 75 seconds and the advertised heartbeat interval is 30 seconds.

Stable private close codes are: `4001 AUTH_FAILED`, `4002 INVALID_MESSAGE`, `4004
INSTALLATION_MISMATCH`, `4005 HANDSHAKE_TIMEOUT`, `4006 LIVENESS_TIMEOUT`, and
`4008 SESSION_REPLACED`. A newer authenticated session replaces the previous
in-process session for the same installation. A distributed registry/notification
adapter is required before running multiple WebSocket workers.

The Connector uses capped full-jitter reconnect delays. Authentication and
installation-binding failures stop the loop and initiate Home Assistant reauth;
protocol errors stop without retry; network loss and liveness timeouts reconnect.
