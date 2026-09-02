"""Pagina protetta per revisionare l'apprendimento del laboratorio Alexa."""

# HTML inline e lettura di un modello statico sono intenzionali per questa pagina isolata.
# ruff: noqa: E501, ASYNC240

from __future__ import annotations

import copy
import hmac
import html
import json
import re
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .conversation_learning import ConversationLearningStore
from .database import get_database_session
from .domain.models import Installation, Tenant

router = APIRouter(prefix="/laboratory/learning", tags=["laboratory-learning"])
security = HTTPBasic()

_INTENT_MAP = {
    "photovoltaic_power": "PhotovoltaicIntent",
    "consumption_power": "ConsumptionIntent",
    "battery_level": "BatteryIntent",
    "grid_power": "GridPowerIntent",
    "temperature": "TemperatureIntent",
    "acs_temperature": "TemperatureIntent",
    "exported_energy_today": "ExportedEnergyIntent",
    "imported_energy_today": "ImportedEnergyIntent",
    "alarm_status": "AlarmStatusIntent",
    "lock_summary": "LockSummaryIntent",
    "lock_status": "LockStatusIntent",
    "opening_summary": "OpeningSummaryIntent",
    "opening_status": "OpeningStatusIntent",
}


def _alexa_intent(intent: str, canonical: str) -> str:
    normalized = canonical.casefold()
    collective = any(word in normalized.split() for word in ("tutti", "tutte", "entrambi", "entrambe"))
    collective_intents = {
        "battery_level": "BatterySummaryIntent",
        "consumption_power": "ConsumptionSummaryIntent",
        "temperature": "TemperatureSummaryIntent",
        "grid_power": "GridPowerSummaryIntent",
        "exported_energy_today": "ExportedEnergySummaryIntent",
        "imported_energy_today": "ImportedEnergySummaryIntent",
    }
    if intent == "photovoltaic_power" and "sas" in normalized and "privato" in normalized:
        return "CombinedPhotovoltaicIntent"
    if collective and intent in collective_intents:
        return collective_intents[intent]
    return _INTENT_MAP.get(intent, intent)


def _authorize(credentials: Annotated[HTTPBasicCredentials, Depends(security)]) -> None:
    settings = get_settings()
    valid = bool(settings.laboratory_learning_password) and hmac.compare_digest(
        credentials.username.encode(), settings.laboratory_learning_username.encode()
    ) and hmac.compare_digest(
        credentials.password.encode(), settings.laboratory_learning_password.encode()
    )
    if not valid:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Credenziali non valide",
            headers={"WWW-Authenticate": "Basic"},
        )


auth_dependency = Depends(_authorize)
session_dependency = Depends(get_database_session)


async def _tenant(session: AsyncSession) -> Tenant:
    slug = get_settings().alexa_laboratory_tenant_slug
    tenant = await session.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant laboratorio non trovato")
    return tenant


def _store() -> ConversationLearningStore:
    settings = get_settings()
    return ConversationLearningStore(
        settings.redis_url, settings.conversation_learning_ttl_days * 24 * 60 * 60
    )


def _record_actions(key: str, approved: bool) -> str:
    safe_key = html.escape(key, quote=True)
    approve = (
        "<span>Approvata</span>"
        if approved
        else f'<form method="post" action="/laboratory/learning/approve"><input type="hidden" name="key" value="{safe_key}"><button>Approva</button></form>'
    )
    delete = f'<form method="post" action="/laboratory/learning/delete" onsubmit="return confirm(\'Eliminare questa frase?\')"><input type="hidden" name="key" value="{safe_key}"><button class="danger">Elimina</button></form>'
    return f'<div class="actions">{approve}{delete}</div>'


@router.get("", response_class=HTMLResponse)
async def learning_page(
    _: Annotated[None, auth_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> HTMLResponse:
    tenant = await _tenant(session)
    records = await _store().list_for_tenant(tenant.id)
    installations = {
        str(item.id): item.name
        for item in (
            await session.scalars(
                select(Installation).where(Installation.tenant_id == tenant.id)
            )
        ).all()
    }
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(record.utterance)}</td>"
        f"<td>{html.escape(record.canonical)}</td>"
        f"<td>{html.escape(_alexa_intent(record.intent, record.canonical))}</td>"
        f"<td>{html.escape(installations.get(record.installation_id, record.installation_id))}</td>"
        f"<td>{record.hits}</td>"
        f"<td>{_record_actions(record.key, record.approved)}</td>"
        "</tr>"
        for record in records
    )
    body = f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Apprendimento IA · Ekonex</title>
<style>body{{font:15px system-ui;margin:0;background:#f4f6f9;color:#17202a}}main{{max-width:1400px;margin:auto;padding:28px}}table{{width:100%;border-collapse:collapse;background:white}}th,td{{padding:12px;border-bottom:1px solid #ddd;text-align:left}}button,a.button{{background:#1769e0;color:white;border:0;border-radius:7px;padding:9px 12px;text-decoration:none;cursor:pointer}}button.danger{{background:#c62828}}.actions{{display:flex;align-items:center;gap:8px}}.actions form{{margin:0}}.cards{{display:flex;gap:14px;margin:18px 0}}.card{{background:white;padding:18px;border-radius:10px}}</style></head><body><main>
<h1>Apprendimento IA</h1><p>Le frasi vengono apprese solo dopo una risposta valida. L'approvazione le inserisce nella bozza JSON Alexa.</p>
<div class="cards"><div class="card"><b>{len(records)}</b><br>Frasi apprese</div><div class="card"><b>{sum(item.approved for item in records)}</b><br>Approvate</div></div>
<p><a class="button" href="/laboratory/learning/model.json">Scarica JSON Alexa aggiornato</a></p>
<table><thead><tr><th>Frase pronunciata</th><th>Interpretazione</th><th>Intent Alexa</th><th>Impianto</th><th>Riutilizzi</th><th>Stato</th></tr></thead><tbody>{rows or '<tr><td colspan=6>Nessuna frase ancora appresa</td></tr>'}</tbody></table>
</main></body></html>"""
    return HTMLResponse(body)


@router.post("/approve", response_class=RedirectResponse)
async def approve_phrase(
    _: Annotated[None, auth_dependency],
    session: Annotated[AsyncSession, session_dependency],
    request: Request,
) -> RedirectResponse:
    fields = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    key = fields.get("key", [""])[0][:500]
    tenant = await _tenant(session)
    if not await _store().approve(tenant.id, key):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Frase non trovata")
    return RedirectResponse("/laboratory/learning", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/delete", response_class=RedirectResponse)
async def delete_phrase(
    _: Annotated[None, auth_dependency],
    session: Annotated[AsyncSession, session_dependency],
    request: Request,
) -> RedirectResponse:
    fields = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    key = fields.get("key", [""])[0][:500]
    tenant = await _tenant(session)
    if not await _store().delete(tenant.id, key):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Frase non trovata")
    return RedirectResponse("/laboratory/learning", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/model.json", response_class=JSONResponse)
async def download_model(
    _: Annotated[None, auth_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> JSONResponse:
    tenant = await _tenant(session)
    records = await _store().list_for_tenant(tenant.id)
    model_path = Path(__file__).resolve().parents[3] / "config" / "alexa_laboratory_interaction_model_it_IT.json"
    model = copy.deepcopy(json.loads(model_path.read_text(encoding="utf-8")))
    intents = {
        item["name"]: item
        for item in model["interactionModel"]["languageModel"]["intents"]
    }
    for record in records:
        intent_name = _alexa_intent(record.intent, record.canonical)
        if not record.approved or intent_name not in intents:
            continue
        sample = re.sub(r"[^\wÀ-ÿ' ]+", " ", record.utterance, flags=re.UNICODE)
        sample = re.sub(r"\s+", " ", sample).strip().casefold()
        samples = intents[intent_name].setdefault("samples", [])
        if sample and sample not in {item.casefold() for item in samples}:
            samples.append(sample)
    return JSONResponse(
        model,
        headers={"Content-Disposition": 'attachment; filename="ekonex-laboratorio-it-IT.json"'},
    )
