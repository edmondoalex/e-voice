# ruff: noqa: E501
"""HTTP contract for official installation-bound Wear devices."""

from __future__ import annotations

import html
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .conversation_service import ConversationEntityService
from .database import get_database_session
from .domain.models import Installation, WearDevice
from .evcp import LIVENESS_TIMEOUT_SECONDS
from .installation_bound import BoundContext
from .installation_bound_api import _frame_policy, _ui_csrf, bound_context_dependency
from .wear_devices import CAPABILITIES, WearDenied, WearDeviceService

router = APIRouter(tags=["wear"])
session_dependency = Depends(get_database_session)


class EnrollmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capabilities: set[str] = Field(default_factory=lambda: set(CAPABILITIES))


class EnrollmentResponse(BaseModel):
    enrollment_id: str
    code: str
    expires_at: datetime


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    code: str = Field(min_length=20, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)


class ClaimResponse(BaseModel):
    device_id: str
    credential: str


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    utterance: str = Field(min_length=1, max_length=300)


async def _wear_context(
    session: Annotated[AsyncSession, session_dependency],
    authorization: Annotated[str | None, Header()] = None,
):
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wear_credential_denied")
    try:
        context = await WearDeviceService(session).context(authorization[7:])
    except WearDenied as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wear_credential_denied") from error
    return context


wear_context_dependency = Depends(_wear_context)


@router.post("/bounded/wear/enrollments", response_model=EnrollmentResponse)
async def create_enrollment(
    payload: EnrollmentRequest,
    context: Annotated[BoundContext, bound_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
    x_ekonex_csrf: Annotated[str | None, Header()] = None,
) -> EnrollmentResponse:
    context.require("use")
    if x_ekonex_csrf != _ui_csrf(context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "csrf_denied")
    try:
        enrollment, code = await WearDeviceService(session).create_enrollment(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            installation_id=context.effective_installation_id,
            created_by_user_id=context.user_id,
            capabilities=payload.capabilities,
        )
        await session.commit()
    except WearDenied as error:
        await session.rollback()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "wear_enrollment_denied") from error
    return EnrollmentResponse(
        enrollment_id=str(enrollment.id), code=code, expires_at=enrollment.expires_at
    )


@router.get("/bounded/wear", response_class=HTMLResponse)
async def wear_management(
    context: Annotated[BoundContext, bound_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> HTMLResponse:
    context.require("use")
    devices = (await session.scalars(select(WearDevice).where(
        WearDevice.tenant_id == context.tenant_id,
        WearDevice.user_id == context.user_id,
        WearDevice.installation_id == context.effective_installation_id,
        WearDevice.revoked_at.is_(None),
    ).order_by(WearDevice.created_at.desc()))).all()
    rows = "".join(
        f'<li><strong>{html.escape(item.display_name)}</strong> '
        f'<button data-device="{item.id}">Revoca</button></li>'
        for item in devices
    ) or "<li>Nessun orologio associato.</li>"
    capabilities = ",".join(sorted(CAPABILITIES))
    page = f"""<!doctype html><html lang=it><head><meta charset=utf-8>
<meta name=viewport content='width=device-width,initial-scale=1'><title>Google Wear</title>
<style>body{{font:16px system-ui;background:#101619;color:#f4f7f8;margin:0;padding:24px}}button{{padding:10px 14px;border:1px solid #41cbea;border-radius:10px;background:#142126;color:#fff}}code{{font-size:18px;color:#41cbea}}li{{margin:10px 0}}</style></head><body>
<h1>Google Wear</h1><p>Orologi legati a questo utente e a questo impianto.</p>
<button id=create>Genera codice associazione</button><p id=result role=status></p>
<h2>Dispositivi attivi</h2><ul>{rows}</ul>
<script>const result=document.querySelector('#result');document.querySelector('#create').onclick=async()=>{{
const csrf=(await(await fetch('/bounded/ui/csrf')).json()).csrf_token;const response=await fetch('/bounded/wear/enrollments',{{method:'POST',headers:{{'Content-Type':'application/json','X-Ekonex-CSRF':csrf}},body:JSON.stringify({{capabilities:'{capabilities}'.split(',')}})}});const value=await response.json();result.innerHTML=response.ok?'Codice monouso (10 minuti): <code>'+value.code+'</code>':'Associazione non disponibile';}};</script>
<script>document.querySelectorAll('[data-device]').forEach(button=>button.onclick=async()=>{{if(!confirm('Revocare questo orologio?'))return;const csrf=(await(await fetch('/bounded/ui/csrf')).json()).csrf_token;const response=await fetch('/bounded/wear/devices/'+button.dataset.device,{{method:'DELETE',headers:{{'X-Ekonex-CSRF':csrf}}}});if(response.ok)location.reload();else result.textContent='Revoca non riuscita';}});</script>
</body></html>"""
    response = HTMLResponse(page)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    frame_ancestors, _same_site = _frame_policy()
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        f"connect-src 'self'; frame-ancestors {frame_ancestors}"
    )
    return response


@router.delete("/bounded/wear/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_device(
    device_id: str,
    context: Annotated[BoundContext, bound_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
    x_ekonex_csrf: Annotated[str | None, Header()] = None,
) -> None:
    context.require("use")
    if x_ekonex_csrf != _ui_csrf(context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "csrf_denied")
    try:
        from uuid import UUID
        parsed = UUID(device_id)
    except ValueError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND) from error
    device = await session.scalar(select(WearDevice).where(
        WearDevice.id == parsed,
        WearDevice.tenant_id == context.tenant_id,
        WearDevice.user_id == context.user_id,
        WearDevice.installation_id == context.effective_installation_id,
        WearDevice.revoked_at.is_(None),
    ))
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    device.revoked_at = datetime.now(UTC)
    await session.commit()


@router.post("/api/wear/v1/enrollments/claim", response_model=ClaimResponse)
async def claim(
    payload: ClaimRequest,
    session: Annotated[AsyncSession, session_dependency],
) -> ClaimResponse:
    try:
        device, credential = await WearDeviceService(session).claim(
            payload.code, payload.display_name
        )
        await session.commit()
    except WearDenied as error:
        await session.rollback()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "wear_claim_denied") from error
    return ClaimResponse(device_id=str(device.id), credential=credential)


@router.get("/api/wear/v1/bootstrap")
async def bootstrap(context=wear_context_dependency) -> dict[str, object]:
    return {
        "device_id": str(context.device_id),
        "capabilities": sorted(context.capabilities),
    }


@router.post("/api/wear/v1/ask")
async def ask(
    payload: AskRequest,
    context=wear_context_dependency,
    session: Annotated[AsyncSession, session_dependency] = None,
) -> dict[str, str]:
    context.require("voice.ask")
    installation = await session.scalar(select(Installation).where(
        Installation.id == context.installation_id,
        Installation.tenant_id == context.tenant_id,
        Installation.revoked_at.is_(None),
    ))
    if installation is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "installation_unavailable")
    last_seen = installation.last_seen_at
    if last_seen is None or (last_seen if last_seen.tzinfo else last_seen.replace(tzinfo=UTC)) < (
        datetime.now(UTC) - timedelta(seconds=LIVENESS_TIMEOUT_SECONDS)
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "installation_offline")
    reply = await ConversationEntityService(session).ask_for_scope(
        context.tenant_id,
        context.installation_id,
        payload.utterance,
        named_only=False,
        live_installation_public_id=installation.public_id,
        allow_ai_fallback=False,
    )
    await session.commit()
    return {"status": reply.status.value, "speech": reply.speech}
