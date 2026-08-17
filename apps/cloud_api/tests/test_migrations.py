from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from apps.cloud_api.app.config import get_settings


def test_migrations_match_model_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_path = (tmp_path / "migration.db").as_posix()
    monkeypatch.setenv("EKONEX_DATABASE_URL", f"sqlite+aiosqlite:///{database_path}")
    get_settings.cache_clear()
    config = Config("alembic.ini")

    try:
        command.upgrade(config, "head")
        command.check(config)
    finally:
        get_settings.cache_clear()
