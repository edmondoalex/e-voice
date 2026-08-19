"""Environment-backed application settings."""

from functools import lru_cache
from typing import Self

from cryptography.fernet import Fernet
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="EKONEX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    environment: str = "development"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, ge=1, le=65535)
    database_url: str = (
        "postgresql+asyncpg://ekonex:local-development-only@localhost:5432/ekonex_voice"
    )
    redis_url: str = "redis://localhost:6379/0"
    pairing_code_pepper: str = "development-only-pairing-pepper-32-bytes-minimum"
    pairing_delivery_key: str = Fernet.generate_key().decode()
    pairing_portal_csrf_secret: str = "development-only-pairing-csrf-secret-change-me"
    pairing_portal_session_hours: int = Field(default=12, ge=1, le=168)
    state_history_retention_days: int = Field(default=30, ge=1, le=3650)
    operational_event_retention_days: int = Field(default=30, ge=1, le=3650)
    admin_audit_retention_days: int = Field(default=365, ge=30, le=3650)
    portal_login_attempt_retention_days: int = Field(default=30, ge=1, le=365)
    state_history_excluded_domains: str = "sensor"
    alexa_oauth_client_id: str = "ekonex-alexa-development"
    alexa_oauth_client_secret: str = "change-me"
    alexa_redirect_uris: str = "https://pitangui.amazon.com/api/skill/link/DEVELOPMENT"
    alexa_access_token_ttl_seconds: int = Field(default=3600, ge=300, le=86400)
    alexa_lwa_client_id: str = "replace-with-lwa-client-id"
    alexa_lwa_client_secret: str = "replace-with-lwa-secret"
    alexa_token_encryption_key: str = "development-only-change-me"
    alexa_event_gateway_url: str = "https://api.eu.amazonalexa.com/v3/events"

    @model_validator(mode="after")
    def production_pairing_secrets_are_explicit(self) -> Self:
        if self.environment == "production" and (
            self.pairing_portal_csrf_secret.startswith("development-only")
            or len(self.pairing_portal_csrf_secret) < 32
        ):
            raise ValueError("production pairing portal secrets must be explicitly configured")
        return self

    @property
    def excluded_history_domains(self) -> frozenset[str]:
        return frozenset(
            domain.strip().lower()
            for domain in self.state_history_excluded_domains.split(",")
            if domain.strip()
        )


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings for this process."""

    return Settings()
