"""Tests for public legal pages."""

from httpx import ASGITransport, AsyncClient

from apps.cloud_api.app.main import app


async def test_privacy_policy_is_public_and_describes_alexa_data() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/privacy")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Informativa sulla privacy di Ekonex Voice" in response.text
    assert "Amazon Alexa" in response.text
    assert "token OAuth" in response.text
    assert "info@ekonex.it" in response.text


async def test_terms_are_public_and_describe_service_dependencies() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/terms")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Termini di utilizzo di Ekonex Voice" in response.text
    assert "Home Assistant" in response.text
    assert "Amazon Alexa" in response.text
    assert "info@ekonex.it" in response.text
