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

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import AccessDeniedError, TenantContext
from .config import get_settings
from .database import get_database_session
from .domain.enums import PairingStatus
from .domain.models import Tenant
from .pairing import (
    PairingAccessDeniedError,
    PairingExpiredError,
    PairingRateLimitedError,
    PairingService,
    PairingUnavailableError,
)
from .portal_auth import LoginRateLimitedError, PortalAuthenticationService, PortalIdentity
from .services import OperationNotAllowedError

router = APIRouter(tags=["connector-pairing"])
session_dependency = Depends(get_database_session)
CSRF_COOKIE = "ekonex_pair_csrf"
LOGIN_CSRF_COOKIE = "ekonex_login_csrf"
SESSION_COOKIE = "ekonex_portal_session"
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


async def _identity(
    session: Annotated[AsyncSession, session_dependency],
    portal_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> PortalIdentity | None:
    return await PortalAuthenticationService(session).resolve(portal_token)


identity_dependency = Depends(_identity)


async def _tenant_context(
    identity: Annotated[PortalIdentity | None, identity_dependency],
) -> TenantContext:
    if identity is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Autenticazione richiesta")
    if identity.context is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Seleziona un tenant")
    return identity.context


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


def _login_csrf(timestamp: int | None = None, nonce: str | None = None) -> str:
    issued = timestamp or int(time.time())
    random_value = nonce or secrets.token_urlsafe(16)
    value = f"login:{issued}:{random_value}"
    signature = hmac.new(
        get_settings().pairing_portal_csrf_secret.encode(), value.encode(), hashlib.sha256
    ).hexdigest()
    return f"{value}:{signature}"


def _valid_login_csrf(token: str, cookie: str | None) -> bool:
    if cookie is None or not hmac.compare_digest(token, cookie):
        return False
    try:
        marker, issued, nonce, signature = token.split(":", 3)
        timestamp = int(issued)
    except (ValueError, TypeError):
        return False
    if marker != "login" or not nonce or not signature:
        return False
    if timestamp > int(time.time()) + 60 or int(time.time()) - timestamp > 1800:
        return False
    return hmac.compare_digest(token, _login_csrf(timestamp, nonce))


async def _form(request: Request) -> dict[str, str]:
    raw = await request.body()
    if len(raw) > MAX_FORM_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    try:
        values = parse_qs(raw.decode(), strict_parsing=True)
    except (UnicodeDecodeError, ValueError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Dati non validi") from error
    return {key: items[0] for key, items in values.items() if items}


def _cookie(response: Response, name: str, value: str, *, max_age: int) -> None:
    response.set_cookie(
        name,
        value,
        httponly=True,
        secure=get_settings().environment == "production",
        samesite="strict",
        max_age=max_age,
        path="/",
    )


def _login_page(*, csrf: str, message: str = "") -> str:
    notice = f'<p role="alert">{html.escape(message)}</p>' if message else ""
    return f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Accedi · Ekonex Voice</title><style>
.login-logo{{display:block;width:min(100%,280px);height:auto;aspect-ratio:1/1;
object-fit:contain;margin:0 auto;border-radius:12px;background:#050505}}
h1{{font-family:system-ui,sans-serif}}
</style></head><body><main>
<img class="login-logo" src="/static/icon.png" width="64" height="64"
 alt="Ekonex Cloud Voice"><h1>Accedi</h1>{notice}
<form method="post" action="/login">
<input type="hidden" name="csrf_token" value="{html.escape(csrf, quote=True)}">
<label for="email">Email</label><input id="email" name="email" type="email"
 maxlength="320" required autocomplete="username">
<label for="password">Password</label><input id="password" name="password" type="password"
 minlength="12" maxlength="1024" required autocomplete="current-password">
<button type="submit">Accedi</button></form></main></body></html>"""


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    identity: Annotated[PortalIdentity | None, identity_dependency],
) -> Response:
    if identity is not None:
        return RedirectResponse("/pair", status_code=status.HTTP_303_SEE_OTHER)
    token = _login_csrf()
    response = HTMLResponse(_login_page(csrf=token))
    _cookie(response, LOGIN_CSRF_COOKIE, token, max_age=1800)
    return response


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    session: Annotated[AsyncSession, session_dependency],
) -> Response:
    values = await _form(request)
    csrf_token = values.get("csrf_token", "")
    if not _valid_login_csrf(csrf_token, request.cookies.get(LOGIN_CSRF_COOKIE)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    try:
        result = await PortalAuthenticationService(session).login(
            email=values.get("email", ""), password=values.get("password", "")
        )
    except LoginRateLimitedError:
        return HTMLResponse(
            _login_page(csrf=csrf_token, message="Troppi tentativi. Riprova più tardi."),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    if result is None:
        return HTMLResponse(
            _login_page(csrf=csrf_token, message="Credenziali non valide"),
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    token, _ = result
    response = RedirectResponse("/pair", status_code=status.HTTP_303_SEE_OTHER)
    _cookie(
        response,
        SESSION_COOKIE,
        token,
        max_age=get_settings().pairing_portal_session_hours * 3600,
    )
    response.delete_cookie(LOGIN_CSRF_COOKIE, path="/")
    return response


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
.brand-logo{{display:block;width:min(100%,280px);height:auto;aspect-ratio:1/1;
object-fit:contain;margin:0 auto;border-radius:12px;background:#050505}}
h1{{font-size:1.7rem;margin:.75rem 0 .5rem}}p{{line-height:1.5}}
label{{display:block;font-weight:650;margin:18px 0 7px}}
input{{width:100%;font:inherit;padding:13px;border:1px solid #9aafa9;border-radius:9px}}
input[name=code]{{text-transform:uppercase;letter-spacing:.12em;
font-family:ui-monospace,monospace}}
button{{width:100%;margin-top:22px;padding:14px;border:0;border-radius:9px;
background:var(--brand);color:white;font:700 1rem system-ui;cursor:pointer}}
.notice{{padding:12px;border-radius:9px;margin:16px 0}}.success{{background:#dcf7e9}}
.error{{background:#fde8e7;color:#85221d}}small{{display:block;margin-top:16px;color:#52655f}}
</style></head><body><main><img class="brand-logo" src="/static/icon.png"
 width="64" height="64" alt="Ekonex Cloud Voice">
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
<small>Il codice è monouso e scade automaticamente.</small>
<form method="post" action="/logout">
<input type="hidden" name="csrf_token" value="{escaped_csrf}">
<button type="submit">Esci</button></form></main></body></html>"""


async def _tenant_page(
    session: AsyncSession, identity: PortalIdentity, csrf: str, message: str = ""
) -> str:
    options: list[str] = []
    for membership in identity.memberships:
        tenant = await session.get(Tenant, membership.tenant_id)
        if tenant is not None:
            options.append(f'<option value="{tenant.id}">{html.escape(tenant.name)}</option>')
    notice = f'<p role="alert">{html.escape(message)}</p>' if message else ""
    return f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Scegli installazione · Ekonex Voice</title></head><body><main>
<strong>EKONEX VOICE</strong><h1>Scegli cliente/tenant</h1>{notice}
<form method="post" action="/pair/select-tenant">
<input type="hidden" name="csrf_token" value="{html.escape(csrf, quote=True)}">
<label for="tenant_id">Tenant autorizzato</label><select id="tenant_id" name="tenant_id">
{"".join(options)}</select><button type="submit">Continua</button></form></main></body></html>"""


@router.post("/pair/select-tenant")
async def select_pair_tenant(
    request: Request,
    identity: Annotated[PortalIdentity | None, identity_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> Response:
    if identity is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    values = await _form(request)
    current_context = TenantContext(
        user_id=identity.user.id,
        tenant_id=identity.session.selected_tenant_id or UUID(int=0),
        role=identity.memberships[0].role,
    )
    csrf_token = values.get("csrf_token", "")
    if not _valid_csrf(csrf_token, request.cookies.get(CSRF_COOKIE), current_context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    try:
        tenant_id = UUID(values.get("tenant_id", ""))
        await PortalAuthenticationService(session).select_tenant(identity, tenant_id)
    except (ValueError, AccessDeniedError):
        return HTMLResponse(
            await _tenant_page(session, identity, csrf_token, "Tenant non autorizzato"),
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return RedirectResponse("/pair", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
async def logout(
    request: Request,
    identity: Annotated[PortalIdentity | None, identity_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> Response:
    if identity is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    values = await _form(request)
    context = identity.context or TenantContext(
        identity.user.id,
        identity.session.selected_tenant_id or UUID(int=0),
        identity.memberships[0].role,
    )
    if not _valid_csrf(values.get("csrf_token", ""), request.cookies.get(CSRF_COOKIE), context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    await PortalAuthenticationService(session).logout(identity)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return response


@router.get("/pair", response_class=HTMLResponse)
async def pair_page(
    identity: Annotated[PortalIdentity | None, identity_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> Response:
    if identity is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    context = identity.context or TenantContext(
        identity.user.id, UUID(int=0), identity.memberships[0].role
    )
    token = _csrf(context)
    content = (
        _page(csrf=token)
        if identity.context is not None
        else await _tenant_page(session, identity, token)
    )
    response = HTMLResponse(content)
    response.set_cookie(
        CSRF_COOKIE,
        token,
        httponly=True,
        secure=get_settings().environment == "production",
        samesite="strict",
        max_age=1800,
        path="/",
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
