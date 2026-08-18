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
    message_type, message_id, payload = parse_message(raw)
    if message_type != expected_type:
        raise ValueError("unsupported_message")
    if message_id != expected_id:
        raise ValueError("invalid_correlation")
    return payload


def parse_message(raw: str) -> tuple[str, str, dict[str, Any]]:
    """Parse one bounded EVCP envelope without accepting extra fields."""
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
    if value["version"] != PROTOCOL_VERSION or not isinstance(value["type"], str):
        raise ValueError("unsupported_message")
    if not isinstance(value["id"], str) or not isinstance(value["payload"], dict):
        raise ValueError("invalid_correlation")
    UUID(str(value["id"]))
    datetime.fromisoformat(str(value["timestamp"]).replace("Z", "+00:00"))
    return value["type"], value["id"], value["payload"]


def parse_command(payload: dict[str, Any]) -> tuple[str, str, str, dict[str, object]]:
    """Validate the fixed outer command shape; mapper validates typed arguments."""
    if set(payload) != {"session_id", "command_id", "registry_id", "command"}:
        raise ValueError("invalid_command")
    session_id, command_id, registry_id, command = (
        payload["session_id"],
        payload["command_id"],
        payload["registry_id"],
        payload["command"],
    )
    UUID(str(session_id))
    UUID(str(command_id))
    if not isinstance(registry_id, str) or not 1 <= len(registry_id) <= 64:
        raise ValueError("invalid_command")
    if not isinstance(command, dict) or not 1 <= len(command) <= 4:
        raise ValueError("invalid_command")
    if not all(isinstance(key, str) and len(key) <= 64 for key in command):
        raise ValueError("invalid_command")
    return str(session_id), str(command_id), registry_id, command
