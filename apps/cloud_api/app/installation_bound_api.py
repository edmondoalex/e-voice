"""HTTP exchange and dependency for CHANGE028 bounded sessions."""

import csv
import html
import io
import json
import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .database import get_database_session
from .installation_bound import BoundContext, BoundLaunchDenied, InstallationBoundService
from .installation_bound import BoundedResourceService
from .command_dispatch import CommandDispatchService, command_adapter
from .evcp import sessions
from .domain.models import Entity
from .domain.models import AuditEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bounded", tags=["econtrol-installation-bound"])
BOUND_COOKIE = "ekonex_installation_bound"


class LaunchExchangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    launch: str = Field(min_length=32, max_length=8192)


class BoundContextResponse(BaseModel):
    effective_installation_id: str
    capabilities: list[str]
    correlation_id: str


class PreparedLaunchResponse(BaseModel):
    launch_url: str


class BoundCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    installation_id: UUID
    registry_id: str = Field(min_length=1, max_length=255)
    command: dict[str, Any]


def _service(session: AsyncSession) -> InstallationBoundService:
    settings = get_settings()
    return InstallationBoundService(
        session,
        issuer=settings.econtrol_launch_issuer,
        audience=settings.econtrol_launch_audience,
        secret=settings.econtrol_launch_secret.encode(),
    )


async def bound_context(
    session: Annotated[AsyncSession, Depends(get_database_session)],
    token: Annotated[str | None, Cookie(alias=BOUND_COOKIE)] = None,
) -> BoundContext:
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    try:
        return await _service(session).context(token)
    except BoundLaunchDenied as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED) from error


bound_context_dependency = Depends(bound_context)


@router.post("/exchange", response_model=BoundContextResponse)
async def exchange(
    payload: LaunchExchangeRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> BoundContextResponse:
    try:
        raw, context = await _service(session).exchange(payload.launch)
        await session.commit()
    except BoundLaunchDenied as error:
        await session.rollback()
        logger.warning("bounded launch denied")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "launch_denied") from error
    response.set_cookie(
        BOUND_COOKIE,
        raw,
        secure=True,
        httponly=True,
        samesite="strict",
        path="/bounded",
        max_age=900,
    )
    return BoundContextResponse(
        effective_installation_id=str(context.effective_installation_id),
        capabilities=sorted(context.capabilities),
        correlation_id=context.correlation_id,
    )


@router.post("/prepare", response_model=PreparedLaunchResponse)
async def prepare_launch(
    payload: LaunchExchangeRequest,
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> PreparedLaunchResponse:
    try:
        code = await _service(session).prepare(payload.launch)
        await session.commit()
    except BoundLaunchDenied as error:
        await session.rollback()
        logger.warning("bounded launch denied")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "launch_denied") from error
    base = get_settings().econtrol_bounded_public_url.rstrip("/")
    if not base.startswith("https://"):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "bounded_origin_invalid")
    return PreparedLaunchResponse(launch_url=f"{base}/bounded/open?code={code}")


@router.get("/open", name="open_bounded_session")
async def open_bounded_session(
    code: str,
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> HTMLResponse:
    try:
        raw = await _service(session).open_prepared(code)
        await session.commit()
    except BoundLaunchDenied as error:
        await session.rollback()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "launch_denied") from error
    response = HTMLResponse(
        "<!doctype html><html><head><meta charset=utf-8>"
        '<meta http-equiv="refresh" content="0;url=/bounded/app">'
        "<title>e-Voice</title></head><body>"
        '<a href="/bounded/app">Continua in e-Voice</a></body></html>'
    )
    response.set_cookie(BOUND_COOKIE, raw, secure=True, httponly=True, samesite="strict",
                        path="/bounded", max_age=900)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    return response


@router.get("/app", response_class=HTMLResponse)
async def bounded_app(
    context: Annotated[BoundContext, bound_context_dependency],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> HTMLResponse:
    resources = BoundedResourceService(session, context)
    installation = await resources.installation(context.effective_installation_id)
    entities = await resources.entities()
    routines = await resources.routines()
    entity_rows = "".join(
        f"<li><strong>{html.escape(item.display_name or item.friendly_name or item.voice_name or item.ha_entity_id)}</strong>"
        f"<small>{html.escape(item.ha_domain)} · {'disponibile' if item.available else 'non disponibile'}</small></li>"
        for item in sorted(entities, key=lambda row: (row.display_name or row.friendly_name or row.ha_entity_id).casefold())[:80]
    ) or "<li><strong>Nessun dispositivo sincronizzato</strong><small>Controlla il connettore Home Assistant.</small></li>"
    routine_rows = "".join(
        f"<li><strong>{html.escape(item.name)}</strong><small>{'attiva' if item.enabled else 'disattivata'}</small></li>"
        for item in sorted(routines, key=lambda row: row.name.casefold())[:40]
    ) or "<li><strong>Nessuna routine vocale</strong><small>Le routine compariranno dopo la sincronizzazione.</small></li>"
    capabilities = " · ".join(html.escape(item) for item in sorted(context.capabilities))
    title = html.escape(installation.name or "Impianto associato")
    body = f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>e-Voice · {title}</title>
<style>:root{{color-scheme:dark;font-family:Inter,system-ui,sans-serif;background:#111416;color:#f4f7f8}}body{{margin:0}}header{{padding:28px clamp(20px,5vw,72px);background:#171c20;border-bottom:1px solid #30383e}}.brand{{color:#45d6ff;font-weight:800;letter-spacing:.08em}}h1{{margin:.35rem 0 .2rem;font-size:clamp(1.7rem,4vw,2.7rem)}}.muted,small{{color:#9fb0ba}}main{{padding:24px clamp(20px,5vw,72px);display:grid;gap:20px;grid-template-columns:repeat(auto-fit,minmax(290px,1fr))}}section{{background:#1a2024;border:1px solid #30383e;border-radius:18px;padding:20px;box-shadow:0 16px 40px #0004}}.summary{{grid-column:1/-1;display:flex;gap:16px;flex-wrap:wrap}}.metric{{min-width:150px;background:#111619;border-radius:14px;padding:14px}}.metric strong{{display:block;font-size:1.8rem;color:#55d9ff}}ul{{list-style:none;margin:0;padding:0;max-height:55vh;overflow:auto}}li{{display:flex;justify-content:space-between;gap:16px;padding:12px 0;border-bottom:1px solid #2b343a}}li small{{text-align:right}}a{{color:#63dcff}}@media(max-width:600px){{li{{display:block}}li small{{display:block;text-align:left;margin-top:4px}}}}</style></head><body>
<header><div class="brand">EKONEX · e-VOICE</div><h1>{title}</h1><p class="muted">Sessione sicura limitata a questo impianto.</p></header>
<main><section class="summary"><div class="metric"><strong>{len(entities)}</strong>Dispositivi</div><div class="metric"><strong>{len(routines)}</strong>Routine vocali</div><div class="metric"><strong>Attiva</strong>Connessione</div></section>
<section><h2>Dispositivi vocali</h2><ul>{entity_rows}</ul></section><section><h2>Routine vocali</h2><ul>{routine_rows}</ul></section>
<section><h2>Permessi sessione</h2><p class="muted">{capabilities}</p><p><a href="/bounded/context">Diagnostica contesto</a></p></section></main></body></html>"""
    response = HTMLResponse(body)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"
    return response


@router.get("/context", response_model=BoundContextResponse)
async def current_context(
    context: Annotated[BoundContext, bound_context_dependency],
) -> BoundContextResponse:
    return BoundContextResponse(
        effective_installation_id=str(context.effective_installation_id),
        capabilities=sorted(context.capabilities),
        correlation_id=context.correlation_id,
    )


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_database_session)],
    token: Annotated[str | None, Cookie(alias=BOUND_COOKIE)] = None,
) -> None:
    if token is not None:
        await _service(session).revoke(token)
        await session.commit()
    response.delete_cookie(BOUND_COOKIE, path="/bounded", secure=True, httponly=True)


@router.get("/installations/{installation_id}/entities")
async def entities(
    installation_id: UUID,
    context: Annotated[BoundContext, bound_context_dependency],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[dict[str, Any]]:
    resources = BoundedResourceService(session, context)
    await resources.installation(installation_id)
    return [
        {"id": str(item.id), "registry_id": item.ha_registry_id, "domain": item.ha_domain}
        for item in await resources.entities()
    ]


@router.get("/installations/{installation_id}/routines")
async def routines(
    installation_id: UUID,
    context: Annotated[BoundContext, bound_context_dependency],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[dict[str, Any]]:
    resources = BoundedResourceService(session, context)
    await resources.installation(installation_id)
    return [{"id": str(item.id), "name": item.name, "enabled": item.enabled} for item in await resources.routines()]


@router.get("/installations/{installation_id}/activity/export")
async def export_activity(
    installation_id: UUID,
    context: Annotated[BoundContext, bound_context_dependency],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    format: str = "json",
) -> Response:
    context.require("export")
    resources = BoundedResourceService(session, context)
    await resources.installation(installation_id)
    rows = [
        {"id": str(item.id), "event_type": item.event_type, "result": item.result}
        for item in await resources.audit_events()
    ]
    if format == "json":
        return Response(json.dumps(rows, separators=(",", ":")), media_type="application/json")
    if format != "csv":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "format_invalid")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=["id", "event_type", "result"])
    writer.writeheader()
    writer.writerows(rows)
    return Response(output.getvalue(), media_type="text/csv")


@router.get("/installations/{installation_id}/events/stream")
async def event_stream(
    installation_id: UUID,
    context: Annotated[BoundContext, bound_context_dependency],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    token: Annotated[str | None, Cookie(alias=BOUND_COOKIE)] = None,
) -> StreamingResponse:
    context.require("stream")
    resources = BoundedResourceService(session, context)
    await resources.installation(installation_id)
    events = await resources.operational_events()

    return StreamingResponse(
        bounded_event_payload(_service(session), token or "", events),
        media_type="text/event-stream",
    )


async def bounded_event_payload(service: InstallationBoundService, token: str, events):
    """Revalidate revocation before every SSE item and terminate fail-closed."""
    for item in events:
        try:
            await service.context(token)
        except BoundLaunchDenied:
            return
        yield f"data: {json.dumps({'id': str(item.id), 'event_type': item.event_type})}\n\n"


@router.post("/commands")
async def command(
    payload: BoundCommandRequest,
    context: Annotated[BoundContext, bound_context_dependency],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    resources = BoundedResourceService(session, context)
    resources.authorize_installation(payload.installation_id, "command")
    entity = await session.scalar(select(Entity).where(
        Entity.installation_id == payload.installation_id,
        Entity.ha_registry_id == payload.registry_id,
    ))
    if entity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    try:
        spec = command_adapter.validate_python(payload.command)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "command_invalid") from error
    outcome = await CommandDispatchService(session, sessions).dispatch(
        payload.installation_id, payload.registry_id, spec
    )
    session.add(AuditEvent(
        tenant_id=context.tenant_id,
        installation_id=context.effective_installation_id,
        user_id=context.user_id,
        source="econtrol_bounded",
        event_type="bounded.command",
        request_id=context.correlation_id,
        payload_redacted_json={
            "registry_id": payload.registry_id,
            "operation": spec.operation,
        },
        result=outcome.status,
    ))
    await session.commit()
    return {"command_id": str(outcome.command_id), "status": outcome.status, "error_code": outcome.error_code}
