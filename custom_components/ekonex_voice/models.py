"""Typed models for the Ekonex Voice integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .client import EkonexVoiceClient
    from .connection import EkonexVoiceConnection


class PairingState(StrEnum):
    """Cloud pairing states understood by M3."""

    PENDING = "pending"
    CLAIMED = "claimed"
    EXPIRED = "expired"
    DENIED = "denied"


class ConnectionState(StrEnum):
    """Safe connection health states exposed to diagnostics."""

    CONNECTING = "connecting"
    ONLINE = "online"
    BACKING_OFF = "backing_off"
    REAUTH_REQUIRED = "reauth_required"
    PROTOCOL_ERROR = "protocol_error"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class PairingSession:
    """Transient pairing material; never persisted in a ConfigEntry."""

    session_id: str
    code: str
    polling_secret: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PairingResult:
    """Result returned while polling a pairing session."""

    state: PairingState
    installation_id: str | None = None
    connector_credential: str | None = None
    installation_name: str | None = None
    tenant_name: str | None = None


@dataclass(slots=True)
class EkonexVoiceRuntimeData:
    """Resources owned by one loaded ConfigEntry."""

    client: EkonexVoiceClient
    connection: EkonexVoiceConnection

    async def async_close(self) -> None:
        """Stop background work and close network resources."""
        await self.connection.async_stop()
        await self.client.async_close()


type EkonexVoiceConfigEntry = ConfigEntry[EkonexVoiceRuntimeData]
