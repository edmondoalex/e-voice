"""Data-only Firebase wake-ups for authenticated Wear devices."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from cryptography.fernet import Fernet, InvalidToken
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from .config import Settings, get_settings

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


class WearFcmSender:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def decrypt(self, encrypted_token: str) -> str:
        try:
            return Fernet(self.settings.pairing_delivery_key.encode()).decrypt(
                encrypted_token.encode()
            ).decode()
        except (InvalidToken, UnicodeDecodeError) as error:
            raise ValueError("invalid encrypted FCM token") from error

    async def send_wake(self, encrypted_token: str, event_id: str) -> bool | None:
        """Return delivery state, invalid-token state, or a retryable-error state."""
        credentials_path = self.settings.wear_fcm_credentials_path
        if not credentials_path or not await asyncio.to_thread(Path(credentials_path).is_file):
            return None
        try:
            token = self.decrypt(encrypted_token)
            credentials = service_account.Credentials.from_service_account_file(
                credentials_path, scopes=[FCM_SCOPE]
            )
            await asyncio.to_thread(credentials.refresh, Request())
            payload = {
                "message": {
                    "token": token,
                    "data": {"type": "wear_notification", "event_id": event_id},
                    "android": {"priority": "high", "ttl": "60s"},
                }
            }
            url = (
                "https://fcm.googleapis.com/v1/projects/"
                f"{self.settings.wear_fcm_project_id}/messages:send"
            )
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {credentials.token}"},
                    json=payload,
                )
            if response.status_code < 300:
                return True
            if response.status_code in {400, 404} and any(
                marker in response.text for marker in ("UNREGISTERED", "INVALID_ARGUMENT")
            ):
                return False
        except (OSError, ValueError, GoogleAuthError, httpx.HTTPError):
            return None
        return None
