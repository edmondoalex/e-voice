"""Browser-facing Alexa OAuth authorization flow backed by Ekonex portal users."""

from __future__ import annotations

import html
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .alexa import _digest, _redirect_allowed, _token
from .config import get_settings
from .database import get_database_session
from .domain.models import AlexaAccountLink, AlexaOAuthGrant, Tenant, TenantMembership
from .pairing_api import (
    LOGIN_CSRF_COOKIE,
    SESSION_COOKIE,
    _cookie,
    _form,
    _login_csrf,
    _valid_login_csrf,
)
from .portal_auth import LoginRateLimitedError, PortalAuthenticationService, PortalIdentity

router = APIRouter(tags=["alexa-account-linking"])
session_dependency = Depends(get_database_session)
portal_token_cookie = Cookie(default=None, alias=SESSION_COOKIE)
user_header = Header(default=None)


def _oauth_values(values: dict[str, str]) -> dict[str, str]:
    return {
        key: values[key]
        for key in (
            "response_type",
            "client_id",
            "redirect_uri",
            "state",
            "scope",
            "code_challenge",
            "code_challenge_method",
        )
        if values.get(key)
    }


def _validate_request(values: dict[str, str]) -> None:
    settings = get_settings()
    if values.get("response_type") != "code":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_request")
    if values.get("client_id") != settings.alexa_oauth_client_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_client")
    if not _redirect_allowed(values.get("redirect_uri", "")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_redirect_uri")
    challenge = values.get("code_challenge")
    if challenge and values.get("code_challenge_method") != "S256":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_request")


def _hidden(values: dict[str, str]) -> str:
    return "".join(
        f'<input type="hidden" name="{html.escape(key, quote=True)}" value="{html.escape(value, quote=True)}">'
        for key, value in values.items()
    )


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} · Ekonex Cloud Voice</title><style>
:root{{--brand:#0b6b53;--ink:#16302a;--bg:#f2f7f5;--muted:#52655f}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:system-ui,sans-serif;min-height:100vh;display:grid;place-items:center;padding:20px}}
main{{width:min(100%,460px);background:white;border-radius:18px;padding:28px;box-shadow:0 12px 36px #1232}}
img{{display:block;width:min(100%,220px);height:auto;aspect-ratio:1/1;object-fit:contain;margin:0 auto 18px;border-radius:12px;background:#050505}}
h1{{font-size:1.6rem;margin:.5rem 0}}p{{line-height:1.5;color:var(--muted)}}label{{display:block;font-weight:650;margin:16px 0 7px}}
input,select{{width:100%;font:inherit;padding:12px;border:1px solid #9aafa9;border-radius:9px}}button{{width:100%;margin-top:20px;padding:13px;border:0;border-radius:9px;background:var(--brand);color:white;font:700 1rem system-ui;cursor:pointer}}
.notice{{padding:12px;border-radius:9px;background:#fde8e7;color:#85221d}}</style></head><body><main>
<img src="/static/ekonex-cloud-voice.png" width="1254" height="1254" alt="Ekonex Cloud Voice">{body}</main></body></html>"""


async def _identity(session: AsyncSession, token: str | None) -> PortalIdentity | None:
    return await PortalAuthenticationService(session).resolve(token)


async def _issue_code(
    session: AsyncSession,
    *,
    user_id: UUID,
    tenant_id: UUID,
    values: dict[str, str],
) -> RedirectResponse:
    membership = await session.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user_id,
        )
    )
    if membership is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "access_denied")
    link = await session.scalar(
        select(AlexaAccountLink).where(
            AlexaAccountLink.tenant_id == tenant_id,
            AlexaAccountLink.user_id == user_id,
        )
    )
    if link is None:
        link = AlexaAccountLink(
            tenant_id=tenant_id,
            user_id=user_id,
            provider_subject=f"ekonex:{user_id}:{tenant_id}",
        )
        session.add(link)
        await session.flush()
    link.status, link.unlinked_at = "active", None
    code = _token("eac_")
    session.add(
        AlexaOAuthGrant(
            link_id=link.id,
            code_hash=_digest(code),
            redirect_uri=values["redirect_uri"],
            code_challenge=values.get("code_challenge"),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    await session.commit()
    return RedirectResponse(
        f"{values['redirect_uri']}?{urlencode({'code': code, 'state': values['state']})}",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/oauth/authorize", response_class=HTMLResponse)
async def authorize(
    request: Request,
    session: Annotated[AsyncSession, session_dependency],
    portal_token: Annotated[str | None, portal_token_cookie] = None,
    x_ekonex_user_id: Annotated[UUID | None, user_header] = None,
) -> Response:
    values = _oauth_values(dict(request.query_params))
    _validate_request(values)

    legacy_tenant = request.query_params.get("tenant_id")
    if x_ekonex_user_id is not None and legacy_tenant:
        return await _issue_code(
            session,
            user_id=x_ekonex_user_id,
            tenant_id=UUID(legacy_tenant),
            values=values,
        )

    identity = await _identity(session, portal_token)
    if identity is None:
        csrf = _login_csrf()
        body = (
            "<h1>Collega Alexa a e-Control</h1><p>Accedi con lo stesso account e-Control che usi per gestire i tuoi impianti.</p>"
            '<form method="post" action="/oauth/alexa/login">'
            + _hidden(values)
            + f'<input type="hidden" name="csrf_token" value="{html.escape(csrf, quote=True)}">'
            '<label for="email">Email</label><input id="email" name="email" type="email" required autocomplete="username">'
            '<label for="password">Password</label><input id="password" name="password" type="password" minlength="12" maxlength="1024" required autocomplete="current-password">'
            "<button type="submit">Accedi e continua</button></form>"
        )
        response = HTMLResponse(_page("Collega Alexa", body))
        _cookie(response, LOGIN_CSRF_COOKIE, csrf, max_age=1800)
        return response

    if identity.context is not None:
        return await _issue_code(
            session,
            user_id=identity.user.id,
            tenant_id=identity.context.tenant_id,
            values=values,
        )

    memberships = identity.memberships
    if not memberships:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "access_denied")
    tenants = {
        tenant.id: tenant
        for tenant in (
            await session.scalars(select(Tenant).where(Tenant.id.in_([m.tenant_id for m in memberships])))
        ).all()
    }
    options = "".join(
        f'<option value="{membership.tenant_id}">{html.escape(tenants[membership.tenant_id].name)}</option>'
        for membership in memberships
        if membership.tenant_id in tenants
    )
    body = (
        "<h1>Scegli impianto/account</h1><p>Alexa verrà collegata esclusivamente al tenant selezionato.</p>"
        '<form method="post" action="/oauth/alexa/tenant">'
        + _hidden(values)
        + f'<label for="tenant_id">Account</label><select id="tenant_id" name="tenant_id" required>{options}</select>'
        "<button type="submit">Collega Alexa</button></form>"
    )
    return HTMLResponse(_page("Scegli account", body))


@router.post("/oauth/alexa/login", response_class=HTMLResponse)
async def login(
    request: Request,
    session: Annotated[AsyncSession, session_dependency],
) -> Response:
    values = await _form(request)
    oauth = _oauth_values(values)
    _validate_request(oauth)
    if not _valid_login_csrf(values.get("csrf_token", ""), request.cookies.get(LOGIN_CSRF_COOKIE)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    try:
        result = await PortalAuthenticationService(session).login(
            email=values.get("email", ""), password=values.get("password", "")
        )
    except LoginRateLimitedError:
        return HTMLResponse(_page("Collega Alexa", '<p class="notice">Troppi tentativi. Riprova più tardi.</p>'), status_code=429)
    if result is None:
        return HTMLResponse(_page("Collega Alexa", '<p class="notice">Credenziali non valide.</p>'), status_code=401)
    token, _ = result
    response = RedirectResponse(
        f"/oauth/authorize?{urlencode(oauth)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    _cookie(
        response,
        SESSION_COOKIE,
        token,
        max_age=get_settings().pairing_portal_session_hours * 3600,
    )
    response.delete_cookie(LOGIN_CSRF_COOKIE, path="/")
    return response


@router.post("/oauth/alexa/tenant")
async def choose_tenant(
    request: Request,
    session: Annotated[AsyncSession, session_dependency],
    portal_token: Annotated[str | None, portal_token_cookie] = None,
) -> Response:
    values = await _form(request)
    oauth = _oauth_values(values)
    _validate_request(oauth)
    identity = await _identity(session, portal_token)
    if identity is None:
        return RedirectResponse(f"/oauth/authorize?{urlencode(oauth)}", status_code=303)
    try:
        tenant_id = UUID(values.get("tenant_id", ""))
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_request") from error
    selected = await PortalAuthenticationService(session).select_tenant(identity, tenant_id)
    return await _issue_code(
        session,
        user_id=selected.user.id,
        tenant_id=tenant_id,
        values=oauth,
    )
