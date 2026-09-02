"""Isolated, read-only Alexa Custom adapter for the Ekonex laboratory."""

from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .conversation_service import ConversationEntityService
from .database import get_database_session
from .domain.models import Installation, Tenant

router = APIRouter(tags=["alexa-laboratory"])
session_dependency = Depends(get_database_session)

_INTENT_PREFIXES = {
    "CombinedPhotovoltaicIntent": "quanto produce il fotovoltaico SAS e privato",
    "BatterySummaryIntent": "percentuale di tutte le batterie",
    "ConsumptionSummaryIntent": "tutti i consumi",
    "TemperatureSummaryIntent": "tutte le temperature",
    "GridPowerSummaryIntent": "potenza rete di tutti gli impianti",
    "ExportedEnergySummaryIntent": "energia oggi esportata da tutti gli impianti",
    "ImportedEnergySummaryIntent": "energia oggi importata da tutti gli impianti",
    "PhotovoltaicIntent": "quanto produce il fotovoltaico",
    "ConsumptionIntent": "quanto consuma",
    "BatteryIntent": "batteria",
    "GridPowerIntent": "potenza rete",
    "TemperatureIntent": "temperatura",
    "ExportedEnergyIntent": "energia oggi esportata",
    "ImportedEnergyIntent": "energia oggi importata",
    "AlarmStatusIntent": "stato allarme",
    "LockSummaryIntent": "stato di tutte le serrature",
    "LockStatusIntent": "stato serratura",
    "OpeningSummaryIntent": "porte o portoni aperti",
    "OpeningStatusIntent": "stato apertura",
}


def _speech(text: str, *, end: bool) -> dict[str, Any]:
    return {
        "version": "1.0",
        "response": {
            "outputSpeech": {"type": "PlainText", "text": text},
            "shouldEndSession": end,
        },
    }


def _application_id(payload: dict[str, Any]) -> str | None:
    session = payload.get("session")
    if isinstance(session, dict):
        application = session.get("application")
        if isinstance(application, dict) and isinstance(application.get("applicationId"), str):
            return str(application["applicationId"])
    return None


def _slot_value(intent: dict[str, Any], name: str) -> str | None:
    slots = intent.get("slots")
    slot = slots.get(name) if isinstance(slots, dict) else None
    resolutions = slot.get("resolutions") if isinstance(slot, dict) else None
    authorities = (
        resolutions.get("resolutionsPerAuthority")
        if isinstance(resolutions, dict)
        else None
    )
    if isinstance(authorities, list):
        for authority in authorities:
            if not isinstance(authority, dict):
                continue
            match_status = authority.get("status")
            if not isinstance(match_status, dict) or match_status.get("code") != "ER_SUCCESS_MATCH":
                continue
            values = authority.get("values")
            if not isinstance(values, list):
                continue
            for candidate in values:
                resolved = candidate.get("value") if isinstance(candidate, dict) else None
                canonical_name = resolved.get("name") if isinstance(resolved, dict) else None
                if isinstance(canonical_name, str) and canonical_name.strip():
                    return canonical_name.strip()
    value = slot.get("value") if isinstance(slot, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _utterance(intent: dict[str, Any]) -> str | None:
    intent_name = intent.get("name")
    if intent_name == "EkonexQueryIntent":
        return _slot_value(intent, "query")
    prefix = _INTENT_PREFIXES.get(str(intent_name))
    if prefix is None:
        return None
    subject = (
        _slot_value(intent, "site")
        or _slot_value(intent, "sensor")
        or _slot_value(intent, "lock")
        or _slot_value(intent, "opening")
    )
    return f"{prefix} {subject}" if subject else prefix


@router.post("/alexa/laboratory")
async def laboratory(
    payload: dict[str, Any],
    database: Annotated[AsyncSession, session_dependency],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.alexa_laboratory_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    expected_auth = f"Bearer {settings.alexa_laboratory_backend_token}"
    if not settings.alexa_laboratory_backend_token or not hmac.compare_digest(
        authorization or "", expected_auth
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    if _application_id(payload) != settings.alexa_laboratory_skill_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN)

    request = payload.get("request")
    request_type = request.get("type") if isinstance(request, dict) else None
    if request_type == "LaunchRequest":
        return _speech("Ciao, sono Ekonex laboratorio. Cosa vuoi sapere?", end=False)
    if request_type != "IntentRequest" or not isinstance(request, dict):
        return _speech("Questa richiesta non è supportata dal laboratorio.", end=True)
    intent = request.get("intent")
    intent_name = intent.get("name") if isinstance(intent, dict) else None
    if intent_name in {"AMAZON.StopIntent", "AMAZON.CancelIntent"}:
        return _speech("Va bene, a presto.", end=True)
    if intent_name == "AMAZON.HelpIntent":
        return _speech(
            "Puoi chiedermi produzione fotovoltaica, consumi, batterie, potenza di rete "
            "e temperature.",
            end=False,
        )
    if intent_name == "AMAZON.FallbackIntent":
        return _speech(
            "Non ho capito la domanda. Prova, quanto produce il fotovoltaico SAS?",
            end=False,
        )
    utterance = _utterance(intent) if isinstance(intent, dict) else None
    if not isinstance(utterance, str) or not utterance.strip():
        return _speech("Non ho capito cosa vuoi sapere.", end=False)

    statement = (
        select(Installation, Tenant)
        .join(Tenant, Tenant.id == Installation.tenant_id)
        .where(
            Tenant.slug == settings.alexa_laboratory_tenant_slug,
            Installation.public_id == settings.alexa_laboratory_installation_public_id,
        )
    )
    row = (await database.execute(statement)).one_or_none()
    if row is None:
        return _speech("L'installazione di laboratorio non è configurata.", end=True)
    installation, tenant = row
    reply = await ConversationEntityService(database).ask_for_scope(
        tenant.id, installation.id, utterance, named_only=True
    )
    return _speech(reply.speech, end=False)
