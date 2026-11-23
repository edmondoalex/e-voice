import pytest
from httpx import ASGITransport, AsyncClient

from apps.cloud_api.app.main import app, application_version


@pytest.mark.asyncio
async def test_health() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": application_version}
