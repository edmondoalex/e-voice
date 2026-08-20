"""Dependency-free Alexa Smart Home proxy for the Ekonex Cloud backend."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

logger = logging.getLogger(__name__)

BACKEND_URL_ENV = "EKONEX_VOICE_BACKEND_URL"
BACKEND_TIMEOUT_ENV = "EKONEX_VOICE_BACKEND_TIMEOUT_SECONDS"
DIRECTIVE_PATH = "/alexa/v1/directive"
MAX_RESPONSE_BYTES = 1_048_576


def _directive_parts(event: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    directive = event.get("directive")
    if not isinstance(directive, dict):
        raise ValueError("missing directive")
    header = directive.get("header")
    if not isinstance(header, dict) or header.get("payloadVersion") != "3":
        raise ValueError("invalid directive header")
    return directive, header


def _access_token(directive: dict[str, Any]) -> str | None:
    endpoint = directive.get("endpoint")
    payload = directive.get("payload")
    scope: object | None = None
    if isinstance(endpoint, dict):
        scope = endpoint.get("scope")
    if not isinstance(scope, dict) and isinstance(payload, dict):
        scope = payload.get("scope")
    if not isinstance(scope, dict):
        return None
    token = scope.get("token")
    return token if isinstance(token, str) and token else None


def _backend_endpoint() -> str:
    base_url = os.environ.get(BACKEND_URL_ENV, "").strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError(f"{BACKEND_URL_ENV} must be an HTTPS origin")
    return f"{base_url}{DIRECTIVE_PATH}"


def _timeout_seconds() -> float:
    try:
        timeout = float(os.environ.get(BACKEND_TIMEOUT_ENV, "8"))
    except ValueError as error:
        raise ValueError(f"{BACKEND_TIMEOUT_ENV} must be numeric") from error
    if not 1 <= timeout <= 20:
        raise ValueError(f"{BACKEND_TIMEOUT_ENV} must be between 1 and 20")
    return timeout


def _error_response(header: dict[str, Any], error_type: str) -> dict[str, Any]:
    response_header: dict[str, str] = {
        "namespace": "Alexa",
        "name": "ErrorResponse",
        "payloadVersion": "3",
        "messageId": str(uuid4()),
    }
    correlation = header.get("correlationToken")
    if isinstance(correlation, str) and correlation:
        response_header["correlationToken"] = correlation
    return {
        "event": {
            "header": response_header,
            "payload": {"type": error_type, "message": error_type},
        }
    }


def _http_error_type(error: HTTPError) -> str:
    if error.code == 401:
        try:
            body = json.loads(error.read(MAX_RESPONSE_BYTES).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = {}
        detail = body.get("detail") if isinstance(body, dict) else None
        if detail == "EXPIRED_AUTHORIZATION_CREDENTIAL":
            return "EXPIRED_AUTHORIZATION_CREDENTIAL"
        return "INVALID_AUTHORIZATION_CREDENTIAL"
    if error.code == 403:
        return "INSUFFICIENT_PERMISSIONS"
    if error.code == 429:
        return "RATE_LIMIT_EXCEEDED"
    return "INTERNAL_ERROR"


def _valid_alexa_response(value: object) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("event"), dict):
        return False
    header = value["event"].get("header")
    return bool(
        isinstance(header, dict)
        and header.get("payloadVersion") == "3"
        and isinstance(header.get("namespace"), str)
        and isinstance(header.get("name"), str)
        and isinstance(header.get("messageId"), str)
    )


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Forward an Alexa v3 directive to Ekonex Cloud and return its Alexa event."""
    del context
    try:
        directive, header = _directive_parts(event)
    except ValueError:
        logger.warning("Rejected malformed Alexa directive")
        return _error_response({}, "INVALID_DIRECTIVE")
    namespace, name = str(header.get("namespace", "")), str(header.get("name", ""))
    if _access_token(directive) is None:
        logger.warning(
            "Alexa directive missing authorization namespace=%s name=%s", namespace, name
        )
        return _error_response(header, "INVALID_AUTHORIZATION_CREDENTIAL")
    try:
        endpoint = _backend_endpoint()
        timeout = _timeout_seconds()
        request = Request(
            endpoint,
            data=json.dumps(event, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "ekonex-voice-lambda/1"},
            method="POST",
        )
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - validated HTTPS origin
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("backend response too large")
        result: object = json.loads(raw.decode("utf-8"))
        if not _valid_alexa_response(result):
            raise ValueError("invalid Alexa response")
    except HTTPError as error:
        error_type = _http_error_type(error)
        logger.warning(
            "Ekonex backend rejected Alexa directive status=%d namespace=%s name=%s",
            error.code,
            namespace,
            name,
        )
        return _error_response(header, error_type)
    except (URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
        logger.exception("Ekonex backend unavailable namespace=%s name=%s", namespace, name)
        return _error_response(header, "INTERNAL_ERROR")
    except ValueError:
        logger.exception("Invalid Lambda/backend configuration or response")
        return _error_response(header, "INTERNAL_ERROR")
    logger.info("Alexa directive completed namespace=%s name=%s", namespace, name)
    return cast(dict[str, Any], result)
