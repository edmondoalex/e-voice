from pytest import MonkeyPatch

from apps.cloud_api.app.config import Settings


def test_settings_read_prefixed_environment(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("EKONEX_ENVIRONMENT", "test")
    monkeypatch.setenv("EKONEX_API_PORT", "9000")

    settings = Settings(_env_file=None)

    assert settings.environment == "test"
    assert settings.api_port == 9000
