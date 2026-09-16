import pytest
from cryptography.fernet import Fernet
from pytest import MonkeyPatch

from apps.cloud_api.app.config import Settings


def test_settings_read_prefixed_environment(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("EKONEX_ENVIRONMENT", "test")
    monkeypatch.setenv("EKONEX_API_PORT", "9000")

    settings = Settings(_env_file=None)

    assert settings.environment == "test"
    assert settings.api_port == 9000


def test_production_rejects_development_pairing_portal_secrets() -> None:
    with pytest.raises(ValueError, match="pairing portal secrets"):
        Settings(environment="production", _env_file=None)


@pytest.mark.parametrize("environment", ["laboratory", "production"])
def test_deployed_environment_requires_explicit_pairing_delivery_key(
    environment: str,
) -> None:
    with pytest.raises(ValueError, match="pairing delivery key must be explicitly configured"):
        Settings(environment=environment, _env_file=None)


def test_laboratory_accepts_explicit_valid_pairing_delivery_key() -> None:
    settings = Settings(
        environment="laboratory",
        pairing_delivery_key=Fernet.generate_key().decode(),
        _env_file=None,
    )

    assert settings.environment == "laboratory"


def test_laboratory_rejects_invalid_pairing_delivery_key() -> None:
    with pytest.raises(ValueError, match="pairing delivery key must be a valid Fernet key"):
        Settings(
            environment="laboratory",
            pairing_delivery_key="not-a-fernet-key",
            _env_file=None,
        )
