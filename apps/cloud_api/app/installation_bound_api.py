"""HTTP exchange and dependency for CHANGE028 bounded sessions."""

import csv
import hashlib
import hmac
import html
import io
import json
import logging
import re
from urllib.parse import parse_qs, urlencode
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
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


def _frame_policy() -> tuple[str, str]:
    raw = get_settings().econtrol_bounded_frame_ancestors
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    if not origins:
        return "'none'", "strict"
    if any(not re.fullmatch(r"https?://[A-Za-z0-9.-]+(?::[0-9]{1,5})?", item) for item in origins):
        return "'none'", "strict"
    return " ".join(origins), "none"
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


UI_OPERATIONS = {
    "light": (("power_on", "Accendi"), ("power_off", "Spegni")),
    "switch": (("power_on", "Accendi"), ("power_off", "Spegni")),
    "cover": (("open", "Apri"), ("stop", "Stop"), ("close", "Chiudi")),
    "scene": (("activate", "Attiva"),),
    "script": (("activate", "Avvia"),),
}


def _ui_csrf(context: BoundContext) -> str:
    secret = get_settings().econtrol_launch_secret.encode()
    return hmac.new(secret, f"bounded-ui-v1\n{context.session_id}".encode(), hashlib.sha256).hexdigest()


def _entity_controls(item: Entity, context: BoundContext) -> str:
    operations = UI_OPERATIONS.get(item.ha_domain, ())
    if "command" not in context.capabilities or not item.available or not operations or not item.ha_registry_id:
        return ""
    buttons = "".join(
        f'<button type="submit" name="operation" value="{operation}">{html.escape(label)}</button>'
        for operation, label in operations
    )
    return (
        '<form method="post" action="/bounded/ui/commands">'
        f'<input type="hidden" name="csrf" value="{_ui_csrf(context)}">'
        f'<input type="hidden" name="registry_id" value="{html.escape(item.ha_registry_id, quote=True)}">'
        f'{buttons}</form>'
    )


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
    frame_ancestors, same_site = _frame_policy()
    response.set_cookie(BOUND_COOKIE, raw, secure=True, httponly=True, samesite=same_site,
                        path="/bounded", max_age=900)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = f"default-src 'none'; base-uri 'none'; frame-ancestors {frame_ancestors}"
    return response


@router.get("/app", response_class=HTMLResponse)
async def bounded_app(
    context: Annotated[BoundContext, bound_context_dependency],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    result: str | None = None,
) -> HTMLResponse:
    resources = BoundedResourceService(session, context)
    installation = await resources.installation(context.effective_installation_id)
    entities = await resources.entities()
    routines = await resources.routines()
    entity_rows = "".join(
        f"<li class=\"device-row\"><span class=\"device-copy\"><strong>{html.escape(item.display_name or item.friendly_name or item.voice_name or item.ha_entity_id)}</strong>"
        f"<small>{html.escape(item.ha_domain)} · {'disponibile' if item.available else 'non disponibile'}</small></span>"
        f"{_entity_controls(item, context)}</li>"
        for item in sorted(entities, key=lambda row: (row.display_name or row.friendly_name or row.ha_entity_id).casefold())[:80]
    ) or "<li><strong>Nessun dispositivo sincronizzato</strong><small>Controlla il connettore Home Assistant.</small></li>"
    routine_rows = "".join(
        f"<li><strong>{html.escape(item.name)}</strong><small>{'attiva' if item.enabled else 'disattivata'}</small></li>"
        for item in sorted(routines, key=lambda row: row.name.casefold())[:40]
    ) or "<li><strong>Nessuna routine vocale</strong><small>Le routine compariranno dopo la sincronizzazione.</small></li>"
    capabilities = " · ".join(html.escape(item) for item in sorted(context.capabilities))
    title = html.escape(installation.name or "Impianto associato")
    feedback = {
        "success": '<p class="feedback ok" role="status">Comando eseguito.</p>',
        "failed": '<p class="feedback error" role="alert">Comando non eseguito. Controlla connessione e dispositivo.</p>',
    }.get(result or "", "")
    body = f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>e-Voice · {title}</title>
<style>:root{{color-scheme:dark;font-family:Inter,system-ui,sans-serif;background:#0e1215;color:#f4f7f8}}*{{box-sizing:border-box}}body{{margin:0}}header{{padding:32px max(24px,calc((100vw - 1400px)/2));background:#171c20;border-bottom:1px solid #30383e}}.brand{{color:#45d6ff;font-weight:800;letter-spacing:.08em}}h1{{margin:.35rem 0 .2rem;font-size:clamp(2rem,4vw,3rem)}}h2{{margin:0 0 18px;font-size:1.35rem}}.muted,small{{color:#9fb0ba}}main{{width:min(1400px,calc(100% - 40px));margin:0 auto;padding:28px 0 48px;display:grid;gap:22px;grid-template-columns:minmax(0,2fr) minmax(310px,1fr);align-items:start}}section{{background:#1a2024;border:1px solid #30383e;border-radius:18px;padding:24px;box-shadow:0 16px 40px #0004}}.summary{{grid-column:1/-1;display:grid;grid-template-columns:repeat(3,minmax(160px,230px));gap:16px}}.metric{{background:#111619;border-radius:14px;padding:16px 18px}}.metric strong{{display:block;font-size:2rem;color:#55d9ff}}.devices{{grid-row:span 2}}.feedback{{grid-column:1/-1;margin:0;padding:13px 16px;border-radius:12px}}.ok{{background:#123d30;color:#9ff2cf}}.error{{background:#54252a;color:#ffd0d3}}ul{{list-style:none;margin:0;padding:0}}.device-list{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;max-height:68vh;overflow:auto;padding-right:8px}}.device-row{{min-height:92px;display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 16px;border:1px solid #2b343a;border-radius:12px;background:#141a1e}}.device-copy{{min-width:0;display:block;text-align:left}}.device-copy strong{{display:block;line-height:1.25}}.device-copy small{{display:block;margin-top:6px}}.simple-list li{{display:flex;justify-content:space-between;gap:16px;padding:12px 0;border-bottom:1px solid #2b343a}}form{{display:flex;justify-content:flex-end;gap:7px;flex-wrap:wrap;flex:0 0 auto}}button{{border:1px solid #45d6ff;background:#10242c;color:#e9fbff;border-radius:9px;padding:8px 12px;font-weight:700;cursor:pointer;white-space:nowrap}}button:hover,button:focus{{background:#174353}}a{{color:#63dcff}}@media(max-width:1100px){{main{{grid-template-columns:1fr}}.devices{{grid-row:auto}}.device-list{{grid-template-columns:1fr}}}}@media(max-width:640px){{header{{padding:24px 20px}}main{{width:min(100% - 24px,1400px);padding-top:16px}}section{{padding:18px}}.summary{{grid-template-columns:1fr}}.device-row{{display:block}}form{{justify-content:flex-start;margin-top:12px}}}}</style></head><body>
<header><div class="brand">EKONEX · e-VOICE</div><h1>{title}</h1><p class="muted">Sessione sicura limitata a questo impianto.</p></header>
<main><section class="summary"><div class="metric"><strong>{len(entities)}</strong>Dispositivi</div><div class="metric"><strong>{len(routines)}</strong>Routine vocali</div><div class="metric"><strong>Attiva</strong>Connessione</div></section>{feedback}
<section class="devices"><h2>Dispositivi vocali</h2><ul class="device-list">{entity_rows}</ul></section><section><h2>Routine vocali</h2><ul class="simple-list">{routine_rows}</ul></section>
<section><h2>Sessione e permessi</h2><p class="muted">{capabilities}</p><p class="session-ok">● Sessione protetta attiva</p><small>Se la sessione scade, chiudi questa scheda e riapri e‑Voice da e‑Control.</small></section></main></body></html>"""
    response = HTMLResponse(body)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    frame_ancestors, _same_site = _frame_policy()
    response.headers["Content-Security-Policy"] = f"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors {frame_ancestors}"
    return response


@router.post("/ui/commands")
async def ui_command(
    request: Request,
    context: Annotated[BoundContext, bound_context_dependency],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RedirectResponse:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/x-www-form-urlencoded":
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
    body = await request.body()
    if len(body) > 4096:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    try:
        values = parse_qs(body.decode("utf-8"), strict_parsing=True)
        if set(values) != {"csrf", "registry_id", "operation"} or any(len(value) != 1 for value in values.values()):
            raise ValueError
        csrf, registry_id, operation = (values[key][0] for key in ("csrf", "registry_id", "operation"))
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT) from None
    if not hmac.compare_digest(csrf, _ui_csrf(context)):
        raise HTTPException(status.HTTP_403_FORBIDDEN)
    entity = await session.scalar(select(Entity).where(
        Entity.installation_id == context.effective_installation_id,
        Entity.ha_registry_id == registry_id,
    ))
    allowed = {item[0] for item in UI_OPERATIONS.get(entity.ha_domain, ())} if entity is not None else set()
    if entity is None or operation not in allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    outcome = await command(BoundCommandRequest(
        installation_id=context.effective_installation_id,
        registry_id=registry_id,
        command={"operation": operation},
    ), context, session)
    query = urlencode({"result": "success" if outcome["status"] == "success" else "failed"})
    response = RedirectResponse(f"/bounded/app?{query}", status_code=status.HTTP_303_SEE_OTHER)
    response.headers["Cache-Control"] = "no-store"
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
