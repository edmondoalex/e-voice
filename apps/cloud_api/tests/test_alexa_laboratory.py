import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app.config import get_settings
from apps.cloud_api.app.database import get_database_session
from apps.cloud_api.app.main import app

SKILL_ID = "amzn1.ask.skill.6f4ff736-deee-43b8-bf09-6399d0f0a4a2"


def launch(skill_id: str = SKILL_ID) -> dict[str, object]:
    return {
        "session": {"application": {"applicationId": skill_id}},
        "request": {"type": "LaunchRequest"},
    }


@pytest.fixture
async def client(session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    async def database_override():  # type: ignore[no-untyped-def]
        yield session

    monkeypatch.setenv("EKONEX_ALEXA_LABORATORY_ENABLED", "true")
    monkeypatch.setenv("EKONEX_ALEXA_LABORATORY_SKILL_ID", SKILL_ID)
    monkeypatch.setenv("EKONEX_ALEXA_LABORATORY_BACKEND_TOKEN", "lab-secret")
    get_settings.cache_clear()
    app.dependency_overrides[get_database_session] = database_override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as value:
        yield value
    app.dependency_overrides.clear()
    get_settings.cache_clear()


async def test_launch_opens_read_only_conversation(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/alexa/laboratory", json=launch(), headers={"Authorization": "Bearer lab-secret"}
    )
    assert response.status_code == 200
    assert response.json()["response"]["shouldEndSession"] is False


async def test_wrong_skill_id_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/alexa/laboratory",
        json=launch("amzn1.ask.skill.other"),
        headers={"Authorization": "Bearer lab-secret"},
    )
    assert response.status_code == 403


async def test_missing_lambda_credential_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post("/alexa/laboratory", json=launch())
    assert response.status_code == 401
