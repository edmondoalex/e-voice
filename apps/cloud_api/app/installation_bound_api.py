"""HTTP exchange and dependency for CHANGE028 bounded sessions."""

import csv
import hashlib
import hmac
import html
import io
import json
import logging
import re
from typing import Annotated, Any
from urllib.parse import parse_qs, urlencode
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .admin_console import (
    DOMAIN_OPERATIONS,
    _command_data,
    _entity_groups,
    _layout,
    _media_source_setting,
    _media_sources,
    _validate_climate_value,
    _validate_domain_command_value,
)
from .command_dispatch import CommandDispatchService, command_adapter
from .config import get_settings
from .database import get_database_session
from .domain.models import AuditEvent, Entity
from .evcp import sessions
from .installation_bound import (
    BoundContext,
    BoundedResourceService,
    BoundLaunchDenied,
    InstallationBoundService,
)
from .laboratory_live_state import load_live_states, overlay_entities

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
BOUND_COOKIE_MAX_AGE = 8 * 60 * 60


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
    return hmac.new(
        secret, f"bounded-ui-v1\n{context.session_id}".encode(), hashlib.sha256
    ).hexdigest()


def _entity_controls(item: Entity, context: BoundContext) -> str:
    operations = UI_OPERATIONS.get(item.ha_domain, ())
    if (
        "command" not in context.capabilities
        or not item.available
        or not operations
        or not item.ha_registry_id
    ):
        return ""
    buttons = "".join(
        f'<button type="submit" name="operation" value="{operation}">{html.escape(label)}</button>'
        for operation, label in operations
    )
    return (
        '<form method="post" action="/bounded/ui/commands">'
        f'<input type="hidden" name="csrf" value="{_ui_csrf(context)}">'
        '<input type="hidden" name="registry_id" '
        f'value="{html.escape(item.ha_registry_id, quote=True)}">'
        f"{buttons}</form>"
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
        max_age=BOUND_COOKIE_MAX_AGE,
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
    next: str = "app",
) -> HTMLResponse:
    try:
        raw = await _service(session).open_prepared(code)
        await session.commit()
    except BoundLaunchDenied as error:
        await session.rollback()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "launch_denied") from error
    destination = "/bounded/wear" if next == "wear" else "/bounded/app"
    response = HTMLResponse(
        "<!doctype html><html><head><meta charset=utf-8>"
        f'<meta http-equiv="refresh" content="0;url={destination}">'
        "<title>e-Voice</title></head><body>"
        f'<a href="{destination}">Continua</a></body></html>'
    )
    frame_ancestors, same_site = _frame_policy()
    response.set_cookie(
        BOUND_COOKIE,
        raw,
        secure=True,
        httponly=True,
        samesite=same_site,
        path="/bounded",
        max_age=BOUND_COOKIE_MAX_AGE,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        f"default-src 'none'; base-uri 'none'; frame-ancestors {frame_ancestors}"
    )
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
    overlay_entities(entities, await load_live_states(installation.public_id))
    csrf = _ui_csrf(context)
    feedback = (
        '<p class="ok" role="status">Comando eseguito.</p>'
        if result == "success"
        else '<p class="bad" role="alert">Comando non eseguito.</p>'
        if result == "failed"
        else ""
    )
    page = _layout(
        installation.name,
        '<p class="muted bounded-scope">Sessione e-Voice legata a questo impianto.</p>'
        + feedback
        + _entity_groups(installation, entities, csrf),
        context,
        csrf,
        "installations",
    )
    page = re.sub(r"<aside>.*?</aside>", "", page, count=1, flags=re.DOTALL)
    page = page.replace("<main>", '<main class="bounded-installation">', 1)
    page = page.replace(
        "</style>",
        "main.bounded-installation{margin-left:0;max-width:none;width:100%;padding:28px}"
        ".bounded-installation>p.muted:first-of-type{display:none}</style>",
        1,
    )
    page = page.replace(f"/installations/{installation.id}/commands", "/bounded/ui/commands")
    page = re.sub(
        rf"/installations/{installation.id}/entities/([0-9a-f-]+)/edit",
        r"/bounded/entities/\1/edit",
        page,
    )
    page = page.replace("fetch('/admin/csrf'", "fetch('/bounded/ui/csrf'")
    for damaged, corrected in (
        ("Entit�", "Entità"),
        ("entit�", "entità"),
        ("Modalit�", "Modalità"),
        ("modalit�", "modalità"),
        ("Umidit�", "Umidità"),
    ):
        page = page.replace(damaged, corrected)
    response = HTMLResponse(page)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    frame_ancestors, _same_site = _frame_policy()
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "img-src data:; connect-src 'self'; base-uri 'none'; form-action 'self'; "
        f"frame-ancestors {frame_ancestors}"
    )
    return response


@router.get("/ui/csrf")
async def bounded_ui_csrf(
    context: Annotated[BoundContext, bound_context_dependency],
) -> dict[str, str]:
    return {"csrf_token": _ui_csrf(context)}


@router.post("/ui/commands")
async def ui_command(
    request: Request,
    context: Annotated[BoundContext, bound_context_dependency],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> Response:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/x-www-form-urlencoded":
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
    body = await request.body()
    if len(body) > 4096:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    try:
        values = parse_qs(body.decode("utf-8"), strict_parsing=True)
        if not {"csrf_token", "entity_id", "operation"}.issubset(values) or any(
            len(value) != 1 for value in values.values()
        ):
            raise ValueError
        csrf, raw_entity_id, operation = (
            values[key][0] for key in ("csrf_token", "entity_id", "operation")
        )
        entity_id = UUID(raw_entity_id)
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT) from None
    if not hmac.compare_digest(csrf, _ui_csrf(context)):
        raise HTTPException(status.HTTP_403_FORBIDDEN)
    entity = await session.scalar(
        select(Entity).where(
            Entity.id == entity_id,
            Entity.installation_id == context.effective_installation_id,
            Entity.deleted_at.is_(None),
        )
    )
    if entity is None or entity.ha_registry_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if operation not in DOMAIN_OPERATIONS.get(entity.ha_domain, set()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Comando non consentito")
    value = values.get("value", [""])[0]
    try:
        if entity.ha_domain == "climate":
            _validate_climate_value(entity, operation, value)
        _validate_domain_command_value(entity, operation, value)
        if operation == "select_source" and (
            value not in _media_sources(entity)
            or _media_source_setting(entity, value).get("enabled", True) is False
        ):
            raise ValueError
        if operation == "media_join":
            member = await session.scalar(
                select(Entity).where(
                    Entity.installation_id == context.effective_installation_id,
                    Entity.ha_registry_id == value,
                    Entity.ha_domain == "media_player",
                    Entity.deleted_at.is_(None),
                    Entity.available.is_(True),
                )
            )
            if member is None or member.id == entity.id:
                raise ValueError
        command_payload = _command_data(operation, value)
        command_adapter.validate_python(command_payload)
    except (ValueError, TypeError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Comando non valido") from error
    outcome = await command(
        BoundCommandRequest(
            installation_id=context.effective_installation_id,
            registry_id=entity.ha_registry_id,
            command=command_payload,
        ),
        context,
        session,
    )
    if "application/json" in request.headers.get("accept", ""):
        succeeded = outcome["status"] == "success"
        payload: dict[str, object] = {
            "ok": succeeded,
            "message": "Comando eseguito" if succeeded else "Comando non riuscito",
            "status": outcome["status"],
            "state": (
                "on"
                if succeeded and operation == "power_on"
                else "off"
                if succeeded and operation == "power_off"
                else None
            ),
        }
        if succeeded and operation in {"set_target_temperature", "set_hvac_mode"}:
            payload["value"] = value
        return Response(json.dumps(payload), media_type="application/json")
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
    return [
        {"id": str(item.id), "name": item.name, "enabled": item.enabled}
        for item in await resources.routines()
    ]


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
    entity = await session.scalar(
        select(Entity).where(
            Entity.installation_id == payload.installation_id,
            Entity.ha_registry_id == payload.registry_id,
        )
    )
    if entity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    try:
        spec = command_adapter.validate_python(payload.command)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "command_invalid") from error
    outcome = await CommandDispatchService(session, sessions).dispatch(
        payload.installation_id, payload.registry_id, spec
    )
    session.add(
        AuditEvent(
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
        )
    )
    await session.commit()
    return {
        "command_id": str(outcome.command_id),
        "status": outcome.status,
        "error_code": outcome.error_code,
    }
