"""Secret-safe async HTTP boundary for Ekonex Voice Cloud."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from aiohttp import ClientError, ClientResponse, ClientSession, ClientWebSocketResponse

from .const import PAIRING_REQUEST_TIMEOUT
from .models import PairingResult, PairingSession, PairingState


class EkonexVoiceError(Exception):
    """Base error whose text is always safe to expose to integration logs."""


class EkonexVoiceCannotConnect(EkonexVoiceError):
    """A temporary network or cloud failure."""


class EkonexVoiceAuthError(EkonexVoiceError):
    """A rejected or revoked Connector credential."""


class EkonexVoiceProtocolError(EkonexVoiceError):
    """A permanent invalid or unsupported cloud response."""


class EkonexVoicePairingExpired(EkonexVoiceError):
    """The short-lived pairing session expired."""


class EkonexVoicePairingDenied(EkonexVoiceError):
    """The pairing session was denied."""


class EkonexVoiceClient:
    """Call the versioned Connector HTTP API without leaking response bodies."""

    def __init__(
        self,
        session: ClientSession,
        cloud_url: str,
        *,
        connector_credential: str | None = None,
        request_timeout: float = PAIRING_REQUEST_TIMEOUT,
    ) -> None:
        self._session = session
        self._cloud_url = cloud_url.rstrip("/")
        self._connector_credential = connector_credential
        self._request_timeout = request_timeout

    async def async_create_pairing_session(self, installation_nonce: str) -> PairingSession:
        """Create a short-lived session for the HA config flow."""
        data = await self._request_json(
            "POST",
            "/connector/v1/pairing/sessions",
            json={"installation_nonce": installation_nonce},
        )
        try:
            expires_at = datetime.fromisoformat(str(data["expires_at"]).replace("Z", "+00:00"))
            return PairingSession(
                session_id=str(data["session_id"]),
                code=str(data["code"]),
                polling_secret=str(data["polling_secret"]),
                expires_at=expires_at,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise EkonexVoiceProtocolError("invalid_pairing_response") from error

    async def async_poll_pairing_session(
        self, session_id: str, polling_secret: str
    ) -> PairingResult:
        """Poll with the transient credential and return only typed fields."""
        data = await self._request_json(
            "GET",
            f"/connector/v1/pairing/sessions/{quote(session_id, safe='')}",
            headers={"Authorization": f"Pairing {polling_secret}"},
        )
        try:
            state = PairingState(str(data["status"]))
        except (KeyError, ValueError) as error:
            raise EkonexVoiceProtocolError("invalid_pairing_status") from error
        if state is PairingState.EXPIRED:
            raise EkonexVoicePairingExpired("pairing_expired")
        if state is PairingState.DENIED:
            raise EkonexVoicePairingDenied("pairing_denied")
        if state is PairingState.CLAIMED:
            required = ("installation_id", "connector_credential")
            if any(not data.get(field) for field in required):
                raise EkonexVoiceProtocolError("incomplete_pairing_claim")
        return PairingResult(
            state=state,
            installation_id=_optional_string(data, "installation_id"),
            connector_credential=_optional_string(data, "connector_credential"),
            installation_name=_optional_string(data, "installation_name"),
            tenant_name=_optional_string(data, "tenant_name"),
        )

    async def async_authenticate(self) -> str:
        """Validate the stored Connector credential and return its installation."""
        if not self._connector_credential:
            raise EkonexVoiceAuthError("missing_connector_credential")
        data = await self._request_json(
            "POST",
            "/connector/v1/auth/validate",
            headers={"Authorization": f"Bearer {self._connector_credential}"},
        )
        installation_id = data.get("installation_id")
        if not isinstance(installation_id, str) or not installation_id:
            raise EkonexVoiceProtocolError("invalid_auth_response")
        return installation_id

    async def async_connect_websocket(self) -> ClientWebSocketResponse:
        """Open the authenticated outbound EVCP transport."""
        if not self._connector_credential:
            raise EkonexVoiceAuthError("missing_connector_credential")
        parts = urlsplit(self._cloud_url)
        scheme = {"https": "wss", "http": "ws"}.get(parts.scheme)
        if scheme is None:
            raise EkonexVoiceProtocolError("invalid_cloud_url")
        url = urlunsplit((scheme, parts.netloc, "/connector/v1/ws", "", ""))
        try:
            async with asyncio.timeout(self._request_timeout):
                return await self._session.ws_connect(
                    url,
                    headers={"Authorization": f"Bearer {self._connector_credential}"},
                    max_msg_size=65_536,
                    heartbeat=None,
                )
        except TimeoutError as error:
            raise EkonexVoiceCannotConnect("cloud_unavailable") from error
        except ClientError as error:
            if getattr(error, "status", None) in {401, 403}:
                raise EkonexVoiceAuthError("invalid_auth") from error
            raise EkonexVoiceCannotConnect("cloud_unavailable") from error

    async def async_close(self) -> None:
        """Release client-owned state.

        The aiohttp session belongs to Home Assistant and is intentionally not
        closed here.
        """

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            async with asyncio.timeout(self._request_timeout):
                response = await self._session.request(
                    method,
                    f"{self._cloud_url}{path}",
                    headers=headers,
                    json=json,
                )
                await _raise_for_status(response)
                payload = await response.json(content_type=None)
        except (TimeoutError, ClientError) as error:
            raise EkonexVoiceCannotConnect("cloud_unavailable") from error
        if not isinstance(payload, dict):
            raise EkonexVoiceProtocolError("invalid_json_object")
        return payload


async def _raise_for_status(response: ClientResponse) -> None:
    """Map HTTP status without reading or exposing a possibly secret body."""
    if response.status in {401, 403}:
        response.release()
        raise EkonexVoiceAuthError("invalid_auth")
    if response.status == 410:
        response.release()
        raise EkonexVoicePairingExpired("pairing_expired")
    if response.status == 409:
        response.release()
        raise EkonexVoicePairingDenied("pairing_denied")
    if response.status >= 500 or response.status in {408, 425, 429}:
        response.release()
        raise EkonexVoiceCannotConnect("cloud_unavailable")
    if response.status >= 400:
        response.release()
        raise EkonexVoiceProtocolError("cloud_request_rejected")


def _optional_string(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) and value else None
