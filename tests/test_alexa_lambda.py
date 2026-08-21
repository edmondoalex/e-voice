"""Deployable Alexa Lambda discovery and authentication tests."""

from __future__ import annotations

import json
import logging
from io import BytesIO
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from aws_lambda.alexa_smart_home.lambda_function import lambda_handler

ACCESS_TOKEN = "eaa_secret-token-never-log"


def discovery(token: str | None = ACCESS_TOKEN) -> dict[str, Any]:
    scope = {"type": "BearerToken", "token": token} if token is not None else {}
    return {
        "directive": {
            "header": {
                "namespace": "Alexa.Discovery",
                "name": "Discover",
                "payloadVersion": "3",
                "messageId": "discovery-request-id",
            },
            "payload": {"scope": scope},
        }
    }


def discover_response() -> dict[str, Any]:
    return {
        "event": {
            "header": {
                "namespace": "Alexa.Discovery",
                "name": "Discover.Response",
                "payloadVersion": "3",
                "messageId": "cloud-response-id",
            },
            "payload": {
                "endpoints": [
                    {
                        "endpointId": "ev1_stable",
                        "manufacturerName": "Ekonex",
                        "friendlyName": "luce ufficio",
                        "description": "e-Control entity via Ekonex Voice",
                        "displayCategories": ["LIGHT"],
                        "cookie": {},
                        "capabilities": [],
                    }
                ]
            },
        }
    }


def accept_grant() -> dict[str, Any]:
    return {
        "directive": {
            "header": {
                "namespace": "Alexa.Authorization",
                "name": "AcceptGrant",
                "payloadVersion": "3",
                "messageId": "accept-grant-request-id",
            },
            "payload": {
                "grant": {
                    "type": "OAuth2.AuthorizationCode",
                    "code": "amazon-one-use-authorization-code",
                },
                "grantee": {"type": "BearerToken", "token": ACCESS_TOKEN},
            },
        }
    }


def accept_grant_response() -> dict[str, Any]:
    return {
        "event": {
            "header": {
                "namespace": "Alexa.Authorization",
                "name": "AcceptGrant.Response",
                "payloadVersion": "3",
                "messageId": "cloud-accept-grant-response-id",
            },
            "payload": {},
        }
    }


class FakeResponse:
    def __init__(self, value: dict[str, Any]) -> None:
        self._body = json.dumps(value).encode()

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self._body[:limit]


def backend_error(status: int, detail: str) -> HTTPError:
    return HTTPError(
        "https://voice.e-control.tech/alexa/v1/directive",
        status,
        "backend error",
        {},
        BytesIO(json.dumps({"detail": detail}).encode()),
    )


def test_discovery_forwards_bearer_directive_and_returns_cloud_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EKONEX_VOICE_BACKEND_URL", "https://voice.e-control.tech")
    expected = discover_response()

    def open_backend(request: Request, timeout: float) -> FakeResponse:
        assert request.full_url == "https://voice.e-control.tech/alexa/v1/directive"
        assert timeout == 8
        assert request.data is not None
        forwarded = json.loads(request.data.decode())
        assert forwarded == discovery()
        assert forwarded["directive"]["payload"]["scope"]["token"] == ACCESS_TOKEN
        return FakeResponse(expected)

    with patch("aws_lambda.alexa_smart_home.lambda_function.urlopen", open_backend):
        assert lambda_handler(discovery(), None) == expected


def test_accept_grant_without_normal_scope_is_forwarded_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EKONEX_VOICE_BACKEND_URL", "https://voice.e-control.tech")
    directive = accept_grant()
    expected = accept_grant_response()

    def open_backend(request: Request, timeout: float) -> FakeResponse:
        assert request.full_url == "https://voice.e-control.tech/alexa/v1/directive"
        assert timeout == 8
        assert request.data is not None
        forwarded = json.loads(request.data.decode())
        assert forwarded == directive
        assert "scope" not in forwarded["directive"]["payload"]
        return FakeResponse(expected)

    with patch("aws_lambda.alexa_smart_home.lambda_function.urlopen", open_backend):
        assert lambda_handler(directive, None) == expected


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ("INVALID_AUTHORIZATION_CREDENTIAL", "INVALID_AUTHORIZATION_CREDENTIAL"),
        ("EXPIRED_AUTHORIZATION_CREDENTIAL", "EXPIRED_AUTHORIZATION_CREDENTIAL"),
    ],
)
def test_backend_invalid_and_expired_tokens_map_to_alexa_errors_without_logging_token(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    detail: str,
    expected: str,
) -> None:
    monkeypatch.setenv("EKONEX_VOICE_BACKEND_URL", "https://voice.e-control.tech")
    with patch(
        "aws_lambda.alexa_smart_home.lambda_function.urlopen",
        side_effect=backend_error(401, detail),
    ):
        response = lambda_handler(discovery(), None)
    assert response["event"]["header"]["name"] == "ErrorResponse"
    assert response["event"]["payload"]["type"] == expected
    assert ACCESS_TOKEN not in caplog.text


def test_missing_token_fails_without_calling_backend(caplog: pytest.LogCaptureFixture) -> None:
    with patch("aws_lambda.alexa_smart_home.lambda_function.urlopen") as open_backend:
        response = lambda_handler(discovery(None), None)
    open_backend.assert_not_called()
    assert response["event"]["payload"]["type"] == "INVALID_AUTHORIZATION_CREDENTIAL"
    assert ACCESS_TOKEN not in caplog.text


def test_cover_directive_logging_is_structured_and_excludes_authorization(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("EKONEX_VOICE_BACKEND_URL", "https://voice.e-control.tech")
    lambda_logger = logging.getLogger("aws_lambda.alexa_smart_home.lambda_function")
    monkeypatch.setattr(lambda_logger, "disabled", False)
    directive = {
        "directive": {
            "header": {
                "namespace": "Alexa.ModeController",
                "name": "SetMode",
                "instance": "Blinds.Position",
                "payloadVersion": "3",
                "messageId": "cover-diagnostic-request",
            },
            "endpoint": {
                "endpointId": "ev1_safe",
                "scope": {"type": "BearerToken", "token": ACCESS_TOKEN},
            },
            "payload": {"mode": "Position.Stopped"},
        }
    }
    expected = discover_response()
    with (
        caplog.at_level(logging.INFO, logger=lambda_logger.name),
        patch(
            "aws_lambda.alexa_smart_home.lambda_function.urlopen",
            return_value=FakeResponse(expected),
        ),
    ):
        lambda_handler(directive, None)
    assert '"endpoint_id":"ev1_safe"' in caplog.text
    assert '"mode":"Position.Stopped"' in caplog.text
    assert ACCESS_TOKEN not in caplog.text


def test_non_accept_grant_authorization_without_scope_is_rejected() -> None:
    directive = accept_grant()
    directive["directive"]["header"]["name"] = "UnsupportedAuthorization"
    with patch("aws_lambda.alexa_smart_home.lambda_function.urlopen") as open_backend:
        response = lambda_handler(directive, None)
    open_backend.assert_not_called()
    assert response["event"]["payload"]["type"] == "INVALID_AUTHORIZATION_CREDENTIAL"


def test_backend_outage_returns_internal_error_without_credentials(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("EKONEX_VOICE_BACKEND_URL", "https://voice.e-control.tech")
    with patch(
        "aws_lambda.alexa_smart_home.lambda_function.urlopen",
        side_effect=URLError("unavailable"),
    ):
        response = lambda_handler(discovery(), None)
    assert response["event"]["payload"]["type"] == "INTERNAL_ERROR"
    assert ACCESS_TOKEN not in caplog.text
