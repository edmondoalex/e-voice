"""Dependency-light EVCP v1 wire models for the Home Assistant Connector."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 65_536


def envelope(message_type: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "version": PROTOCOL_VERSION,
        "type": message_type,
        "id": str(uuid4()),
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "payload": payload,
    }


def parse_ack(raw: str, *, expected_type: str, expected_id: str) -> dict[str, Any]:
    if len(raw.encode()) > MAX_MESSAGE_BYTES:
        raise ValueError("message_too_large")
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {
        "version",
        "type",
        "id",
        "timestamp",
        "payload",
    }:
        raise ValueError("invalid_envelope")
    if value["version"] != PROTOCOL_VERSION or value["type"] != expected_type:
        raise ValueError("unsupported_message")
    if value["id"] != expected_id or not isinstance(value["payload"], dict):
        raise ValueError("invalid_correlation")
    UUID(str(value["id"]))
    datetime.fromisoformat(str(value["timestamp"]).replace("Z", "+00:00"))
    return value["payload"]
