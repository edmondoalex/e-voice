"""Dependency-free proxy for the isolated Alexa Custom laboratory skill."""

from __future__ import annotations

import json
import os
from typing import Any, cast
from urllib.parse import urlparse
from urllib.request import Request, urlopen

BACKEND_PATH = "/alexa/laboratory"


def _application_id(event: dict[str, Any]) -> str | None:
    context = event.get("context")
    if isinstance(context, dict):
        system = context.get("System")
        if isinstance(system, dict):
            application = system.get("application")
            if isinstance(application, dict) and isinstance(application.get("applicationId"), str):
                return cast(str, application["applicationId"])
    session = event.get("session")
    if isinstance(session, dict):
        application = session.get("application")
        if isinstance(application, dict) and isinstance(application.get("applicationId"), str):
            return cast(str, application["applicationId"])
    return None


def _response(text: str, *, end_session: bool = True) -> dict[str, Any]:
    return {
        "version": "1.0",
        "response": {
            "outputSpeech": {"type": "PlainText", "text": text},
            "shouldEndSession": end_session,
        },
    }


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Validate the laboratory Skill ID and proxy only to its staging backend."""
    del context
    expected = os.environ.get("EKONEX_LAB_SKILL_ID", "").strip()
    if not expected or _application_id(event) != expected:
        return _response("Questa richiesta non è autorizzata.")

    base_url = os.environ.get("EKONEX_LAB_BACKEND_URL", "").strip().rstrip("/")
    token = os.environ.get("EKONEX_LAB_BACKEND_TOKEN", "").strip()
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc or not token:
        return _response("Il laboratorio Ekonex non è ancora configurato.")

    request = Request(
        f"{base_url}{BACKEND_PATH}",
        data=json.dumps(event, separators=(",", ":")).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "ekonex-laboratory-lambda/1",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=8) as response:  # noqa: S310 - HTTPS validated above
            value = json.loads(response.read(262_145).decode())
    except Exception as exc:  # Lambda boundary must always return valid Alexa speech.
        print(f"laboratory backend request failed: {type(exc).__name__}: {exc}")
        return _response("Il laboratorio Ekonex non è momentaneamente disponibile.")
    return cast(dict[str, Any], value) if isinstance(value, dict) else _response(
        "Il laboratorio Ekonex ha restituito una risposta non valida."
    )
