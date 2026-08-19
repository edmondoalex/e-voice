"""Tenant-authorized portal plus Home Assistant pairing bootstrap and polling."""

from __future__ import annotations

import hashlib
import hmac
import html
import secrets
import time
from typing import Annotated
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import AccessDeniedError, AuthenticationService, TenantContext
from .config import get_settings
from .database import get_database_session
from .domain.enums import PairingStatus
from .pairing import (
    PairingAccessDeniedError,
    PairingExpiredError,
    PairingRateLimitedError,
    PairingService,
    PairingUnavailableError,
)
from .services import OperationNotAllowedError

router = APIRouter(tags=["connector-pairing"])
session_dependency = Depends(get_database_session)
CSRF_COOKIE = "ekonex_pair_csrf"
MAX_FORM_BYTES = 4096


class PairingSessionCreateRequest(BaseModel):
    installation_nonce: str = Field(min_length=16, max_length=255)


class PairingSessionCreateResponse(BaseModel):
    session_id: str
    code: str
    polling_secret: str
    expires_at: str


class PairingPollResponse(BaseModel):
    status: str
    installation_id: str | None = None
    connector_credential: str | None = None


class PairingClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    code: str = Field(min_length=9, max_length=9, pattern=r"^[A-Za-z2-9]{4}-[A-Za-z2-9]{4}$")
    installation_name: str = Field(min_length=1, max_length=200)


class PairingClaimResponse(BaseModel):
    status: str
    installation_id: UUID


def _service(session: AsyncSession) -> PairingService:
    settings = get_settings()
    return PairingService(
        session,
        code_pepper=settings.pairing_code_pepper.encode(),
        delivery_key=settings.pairing_delivery_key.encode(),
    )


async def _tenant_context(
    session: Annotated[AsyncSession, session_dependency],
    user_id: Annotated[UUID | None, Header(alias="X-Ekonex-User-ID")] = None,
    tenant_id: Annotated[UUID | None, Header(alias="X-Ekonex-Tenant-ID")] = None,
    ingress_secret: Annotated[str | None, Header(alias="X-Ekonex-Ingress-Secret")] = None,
) -> TenantContext:
    """Resolve identity headers set exclusively by the trusted production ingress."""
    expected_secret = get_settings().pairing_portal_ingress_secret
    if ingress_secret is None or not hmac.compare_digest(ingress_secret, expected_secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Autenticazione richiesta")
    if user_id is None or tenant_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Autenticazione richiesta")
    try:
        return await AuthenticationService(session).tenant_context(
            user_id=user_id, tenant_id=tenant_id
        )
    except AccessDeniedError as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Accesso non consentito") from error


context_dependency = Depends(_tenant_context)


@router.post("/connector/v1/pairing/sessions", response_model=PairingSessionCreateResponse)
async def create_pairing_session(
    payload: PairingSessionCreateRequest,
    session: Annotated[AsyncSession, session_dependency],
) -> PairingSessionCreateResponse:
    started = await _service(session).create_session(installation_nonce=payload.installation_nonce)
    return PairingSessionCreateResponse(
        session_id=str(started.session_id),
        code=started.code,
        polling_secret=started.polling_secret,
        expires_at=started.expires_at.isoformat(),
    )


@router.get("/connector/v1/pairing/sessions/{session_id}", response_model=PairingPollResponse)
async def poll_pairing_session(
    session_id: UUID,
    session: Annotated[AsyncSession, session_dependency],
    authorization: Annotated[str | None, Header()] = None,
) -> PairingPollResponse:
    if not authorization or not authorization.startswith("Pairing "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        result = await _service(session).poll(
            session_id=session_id,
            polling_secret=authorization.removeprefix("Pairing ").strip(),
        )
    except PairingAccessDeniedError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from error
    result_status = "denied" if result.status is PairingStatus.LOCKED else result.status.value
    return PairingPollResponse(
        status=result_status,
        installation_id=str(result.installation_id) if result.installation_id else None,
        connector_credential=result.connector_credential,
    )


async def _claim(
    session: AsyncSession, context: TenantContext, payload: PairingClaimRequest
) -> PairingClaimResponse:
    try:
        claimed = await _service(session).claim(
            context, code=payload.code, installation_name=payload.installation_name
        )
    except PairingExpiredError as error:
        raise HTTPException(status.HTTP_410_GONE, "Il codice è scaduto") from error
    except PairingRateLimitedError as error:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Troppi tentativi. Riprova più tardi"
        ) from error
    except PairingUnavailableError as error:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Codice non valido o già utilizzato"
        ) from error
    except OperationNotAllowedError as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Permessi insufficienti") from error
    return PairingClaimResponse(status="paired", installation_id=claimed.installation_id)


@router.post("/connector/v1/pairing/claims", response_model=PairingClaimResponse)
async def claim_pairing_code(
    payload: PairingClaimRequest,
    context: Annotated[TenantContext, context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> PairingClaimResponse:
    """Claim without ever returning the Connector credential."""
    return await _claim(session, context, payload)


def _csrf(context: TenantContext, timestamp: int | None = None, nonce: str | None = None) -> str:
    issued = timestamp or int(time.time())
    random_value = nonce or secrets.token_urlsafe(16)
    value = f"{context.user_id}:{context.tenant_id}:{issued}:{random_value}"
    signature = hmac.new(
        get_settings().pairing_portal_csrf_secret.encode(), value.encode(), hashlib.sha256
    ).hexdigest()
    return f"{value}:{signature}"


def _valid_csrf(token: str, cookie: str | None, context: TenantContext) -> bool:
    if cookie is None or not hmac.compare_digest(token, cookie):
        return False
    try:
        user_id, tenant_id, issued, nonce, signature = token.split(":", 4)
        timestamp = int(issued)
    except (ValueError, TypeError):
        return False
    if user_id != str(context.user_id) or tenant_id != str(context.tenant_id) or not nonce:
        return False
    if timestamp > int(time.time()) + 60 or int(time.time()) - timestamp > 1800:
        return False
    return hmac.compare_digest(token, _csrf(context, timestamp, nonce)) and bool(signature)


def _page(*, csrf: str, message: str = "", success: bool = False) -> str:
    notice = ""
    if message:
        css_class = "success" if success else "error"
        notice = f'<div class="notice {css_class}" role="status">{html.escape(message)}</div>'
    escaped_csrf = html.escape(csrf, quote=True)
    return f"""<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Collega Home Assistant · Ekonex Voice</title><style>
:root{{--brand:#0b6b53;--ink:#16302a;--bg:#f2f7f5}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:system-ui,sans-serif;
min-height:100vh;display:grid;place-items:center;padding:20px}}
main{{width:min(100%,440px);background:white;border-radius:18px;padding:28px;
box-shadow:0 12px 36px #1232}}
.brand{{color:var(--brand);font-weight:800;letter-spacing:.04em}}
h1{{font-size:1.7rem;margin:.5rem 0}}p{{line-height:1.5}}
label{{display:block;font-weight:650;margin:18px 0 7px}}
input{{width:100%;font:inherit;padding:13px;border:1px solid #9aafa9;border-radius:9px}}
input[name=code]{{text-transform:uppercase;letter-spacing:.12em;
font-family:ui-monospace,monospace}}
button{{width:100%;margin-top:22px;padding:14px;border:0;border-radius:9px;
background:var(--brand);color:white;font:700 1rem system-ui;cursor:pointer}}
.notice{{padding:12px;border-radius:9px;margin:16px 0}}.success{{background:#dcf7e9}}
.error{{background:#fde8e7;color:#85221d}}small{{display:block;margin-top:16px;color:#52655f}}
</style></head><body><main><div class="brand">EKONEX VOICE</div>
<h1>Collega Home Assistant</h1>
<p>Inserisci il codice temporaneo mostrato in Home Assistant.</p>{notice}
<form method="post" action="/pair">
<input type="hidden" name="csrf_token" value="{escaped_csrf}">
<label for="code">Codice pairing</label>
<input id="code" name="code" pattern="[A-Za-z2-9]{{4}}-[A-Za-z2-9]{{4}}"
 maxlength="9" placeholder="XXXX-XXXX" required autocomplete="one-time-code">
<label for="installation_name">Nome installazione</label>
<input id="installation_name" name="installation_name" maxlength="200"
 placeholder="Casa Rossi" required autocomplete="organization">
<button type="submit">Collega</button></form>
<small>Il codice è monouso e scade automaticamente.</small></main></body></html>"""


@router.get("/pair", response_class=HTMLResponse)
async def pair_page(context: Annotated[TenantContext, context_dependency]) -> HTMLResponse:
    token = _csrf(context)
    response = HTMLResponse(_page(csrf=token))
    response.set_cookie(
        CSRF_COOKIE,
        token,
        httponly=True,
        secure=get_settings().environment == "production",
        samesite="strict",
        max_age=1800,
        path="/pair",
    )
    return response


@router.post("/pair", response_class=HTMLResponse)
async def pair_submit(
    request: Request,
    context: Annotated[TenantContext, context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> Response:
    raw = await request.body()
    if len(raw) > MAX_FORM_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    try:
        values = parse_qs(raw.decode(), strict_parsing=True)
        csrf_token = values["csrf_token"][0]
        payload = PairingClaimRequest(
            code=values["code"][0], installation_name=values["installation_name"][0]
        )
    except (KeyError, UnicodeDecodeError, ValueError):
        return HTMLResponse(_page(csrf=_csrf(context), message="Dati non validi"), 422)
    if not _valid_csrf(csrf_token, request.cookies.get(CSRF_COOKIE), context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    try:
        await _claim(session, context, payload)
    except HTTPException as error:
        return HTMLResponse(_page(csrf=csrf_token, message=str(error.detail)), error.status_code)
    return HTMLResponse(
        _page(csrf=_csrf(context), message="Home Assistant collegato correttamente.", success=True)
    )
