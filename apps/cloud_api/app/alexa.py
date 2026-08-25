"""Alexa Smart Home v3 adapter and isolated OAuth account-linking boundary."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import math
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from .alexa_discovery_audit import record_discovery
from .command_dispatch import CommandDispatchService, command_adapter
from .config import get_settings
from .cover_modes import COVER_STOP, effective_cover_mode
from .database import get_database_session
from .domain.models import (
    AlexaAccountLink,
    AlexaOAuthGrant,
    AlexaOAuthToken,
    AuditEvent,
    Entity,
    Installation,
    TenantMembership,
)
from .entity_names import effective_voice_name, unambiguous_voice_entities
from .evcp import sessions

router = APIRouter()
database_dependency = Depends(get_database_session)
user_header = Header(default=None)
MAX_DIRECTIVE_BYTES = 65_536
SUPPORTED_DOMAINS = {"light", "switch", "cover", "climate", "fan", "scene"}
_replay: dict[str, dict[str, Any]] = {}
logger = logging.getLogger(__name__)
OFFICE_RANGE_AB_ENTITY_ID = "cover.buspro_cover_porta_ufficio"
HA_TO_ALEXA_THERMOSTAT_MODE = {
    "off": "OFF",
    "heat": "HEAT",
    "cool": "COOL",
    "auto": "AUTO",
    "heat_cool": "AUTO",
    "eco": "ECO",
    "emergency_heat": "EM_HEAT",
}


def alexa_entity_eligible(entity: Entity) -> bool:
    """Return whether an entity has a safe, publishable Alexa representation."""
    return entity.ha_domain in SUPPORTED_DOMAINS and (
        entity.ha_domain != "cover" or effective_cover_mode(entity) is not None
    )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _token(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(48)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _redirect_allowed(uri: str) -> bool:
    return uri in {item.strip() for item in get_settings().alexa_redirect_uris.split(",")}


@router.get("/oauth/authorize")
async def authorize(
    response_type: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    tenant_id: UUID,
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
    x_ekonex_user_id: UUID | None = user_header,
    database: AsyncSession = database_dependency,
) -> RedirectResponse:
    """Issue a one-use code after the existing Ekonex login/consent boundary."""
    settings = get_settings()
    if (
        response_type != "code"
        or client_id != settings.alexa_oauth_client_id
        or not _redirect_allowed(redirect_uri)
        or x_ekonex_user_id is None
        or (code_challenge is not None and code_challenge_method != "S256")
    ):
        raise HTTPException(400, "invalid_request")
    membership = await database.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == x_ekonex_user_id,
        )
    )
    if membership is None:
        raise HTTPException(403, "access_denied")
    link = await database.scalar(
        select(AlexaAccountLink).where(
            AlexaAccountLink.tenant_id == tenant_id,
            AlexaAccountLink.user_id == x_ekonex_user_id,
        )
    )
    if link is None:
        link = AlexaAccountLink(
            tenant_id=tenant_id,
            user_id=x_ekonex_user_id,
            provider_subject=f"ekonex:{x_ekonex_user_id}:{tenant_id}",
        )
        database.add(link)
        await database.flush()
    link.status, link.unlinked_at = "active", None
    code = _token("eac_")
    database.add(
        AlexaOAuthGrant(
            link_id=link.id,
            code_hash=_digest(code),
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    await database.commit()
    return RedirectResponse(f"{redirect_uri}?{urlencode({'code': code, 'state': state})}")


async def _form(request: Request) -> dict[str, str]:
    if int(request.headers.get("content-length", "0")) > 16_384:
        raise HTTPException(413, "request_too_large")
    parsed = parse_qs((await request.body()).decode(), strict_parsing=True)
    return {key: values[0] for key, values in parsed.items()}


def _valid_client(request: Request, form: dict[str, str]) -> bool:
    settings = get_settings()
    client_id, client_secret = form.get("client_id"), form.get("client_secret")
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Basic "):
        try:
            client_id, client_secret = base64.b64decode(authorization[6:]).decode().split(":", 1)
        except (ValueError, UnicodeDecodeError):
            return False
    return hmac.compare_digest(
        client_id or "", settings.alexa_oauth_client_id
    ) and hmac.compare_digest(client_secret or "", settings.alexa_oauth_client_secret)


def _pkce_valid(grant: AlexaOAuthGrant, verifier: str | None) -> bool:
    if grant.code_challenge is None:
        return True
    if verifier is None:
        return False
    encoded = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    return hmac.compare_digest(encoded.decode(), grant.code_challenge)


@router.post("/oauth/token")
async def exchange_token(
    request: Request, database: AsyncSession = database_dependency
) -> JSONResponse:
    form = await _form(request)
    if not _valid_client(request, form):
        raise HTTPException(401, "invalid_client")
    now, link_id = datetime.now(UTC), None
    if form.get("grant_type") == "authorization_code":
        grant = await database.scalar(
            select(AlexaOAuthGrant).where(
                AlexaOAuthGrant.code_hash == _digest(form.get("code", ""))
            )
        )
        if (
            grant is None
            or grant.used_at is not None
            or _utc(grant.expires_at) <= now
            or grant.redirect_uri != form.get("redirect_uri")
            or not _pkce_valid(grant, form.get("code_verifier"))
        ):
            raise HTTPException(400, "invalid_grant")
        grant.used_at, link_id = now, grant.link_id
    elif form.get("grant_type") == "refresh_token":
        old = await database.scalar(
            select(AlexaOAuthToken).where(
                AlexaOAuthToken.refresh_hash == _digest(form.get("refresh_token", "")),
                AlexaOAuthToken.revoked_at.is_(None),
            )
        )
        if old is None:
            raise HTTPException(400, "invalid_grant")
        old.revoked_at, link_id = now, old.link_id
    else:
        raise HTTPException(400, "unsupported_grant_type")
    link = await database.get(AlexaAccountLink, link_id)
    if link is None or link.status != "active":
        raise HTTPException(400, "invalid_grant")
    access, refresh = _token("eaa_"), _token("ear_")
    ttl = get_settings().alexa_access_token_ttl_seconds
    database.add(
        AlexaOAuthToken(
            link_id=link.id,
            access_hash=_digest(access),
            refresh_hash=_digest(refresh),
            access_expires_at=now + timedelta(seconds=ttl),
        )
    )
    await database.commit()
    return JSONResponse(
        {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "bearer",
            "expires_in": ttl,
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/oauth/revoke", status_code=204)
async def revoke(request: Request, database: AsyncSession = database_dependency) -> None:
    form = await _form(request)
    if not _valid_client(request, form):
        raise HTTPException(401, "invalid_client")
    digest = _digest(form.get("token", ""))
    token = await database.scalar(
        select(AlexaOAuthToken).where(
            (AlexaOAuthToken.access_hash == digest) | (AlexaOAuthToken.refresh_hash == digest)
        )
    )
    if token is not None:
        token.revoked_at = datetime.now(UTC)
        await database.commit()


async def _authenticate(token: str, database: AsyncSession) -> AlexaAccountLink:
    row = await database.scalar(
        select(AlexaOAuthToken).where(AlexaOAuthToken.access_hash == _digest(token))
    )
    if row is None or row.revoked_at is not None:
        raise HTTPException(401, "INVALID_AUTHORIZATION_CREDENTIAL")
    if _utc(row.access_expires_at) <= datetime.now(UTC):
        raise HTTPException(401, "EXPIRED_AUTHORIZATION_CREDENTIAL")
    link = await database.get(AlexaAccountLink, row.link_id)
    if link is None or link.status != "active":
        raise HTTPException(401, "INVALID_AUTHORIZATION_CREDENTIAL")
    return link


def _capability(interface: str, properties: list[str] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"type": "AlexaInterface", "interface": interface, "version": "3"}
    if properties:
        value["properties"] = {
            "supported": [{"name": name} for name in properties],
            "proactivelyReported": True,
            "retrievable": True,
        }
    return value


def _is_office_range_ab(entity: Entity) -> bool:
    return entity.ha_domain == "cover" and entity.ha_entity_id == OFFICE_RANGE_AB_ENTITY_ID


def capabilities(entity: Entity) -> list[dict[str, Any]]:
    attributes = entity.attributes_json or {}
    result = [_capability("Alexa"), _capability("Alexa.EndpointHealth", ["connectivity"])]
    if entity.ha_domain in {"light", "switch", "fan"}:
        result.append(_capability("Alexa.PowerController", ["powerState"]))
    if entity.ha_domain == "light":
        result.append(_capability("Alexa.BrightnessController", ["brightness"]))
        if "rgb_color" in attributes:
            result.append(_capability("Alexa.ColorController", ["color"]))
        if "color_temp_kelvin" in attributes:
            result.append(
                _capability("Alexa.ColorTemperatureController", ["colorTemperatureInKelvin"])
            )
    elif entity.ha_domain == "cover":
        if _is_office_range_ab(entity):
            result.append(_capability("Alexa.PowerController", ["powerState"]))
            result.append(
                _capability("Alexa.RangeController", ["rangeValue"])
                | {
                    "instance": "cover.position",
                    "capabilityResources": {
                        "friendlyNames": [
                            {
                                "@type": "text",
                                "value": {"text": "Position", "locale": "en-US"},
                            },
                            {"@type": "asset", "value": {"assetId": "Alexa.Setting.Opening"}},
                        ]
                    },
                    "configuration": {
                        "supportedRange": {
                            "minimumValue": 0,
                            "maximumValue": 100,
                            "precision": 1,
                        },
                        "unitOfMeasure": "Alexa.Unit.Percent",
                    },
                    "semantics": {
                        "actionMappings": [
                            {
                                "@type": "ActionsToDirective",
                                "actions": ["Alexa.Actions.Lower", "Alexa.Actions.Close"],
                                "directive": {
                                    "name": "SetRangeValue",
                                    "payload": {"rangeValue": 0},
                                },
                            },
                            {
                                "@type": "ActionsToDirective",
                                "actions": ["Alexa.Actions.Raise", "Alexa.Actions.Open"],
                                "directive": {
                                    "name": "SetRangeValue",
                                    "payload": {"rangeValue": 100},
                                },
                            },
                        ],
                        "stateMappings": [
                            {
                                "@type": "StatesToValue",
                                "states": ["Alexa.States.Closed"],
                                "value": 0,
                            },
                            {
                                "@type": "StatesToRange",
                                "states": ["Alexa.States.Open"],
                                "range": {"minimumValue": 1, "maximumValue": 100},
                            },
                        ],
                    },
                }
            )
            if entity.supported_features & COVER_STOP:
                result.append(
                    _capability("Alexa.PlaybackController") | {"supportedOperations": ["Stop"]}
                )
            return result
        mode = effective_cover_mode(entity)
        if mode == "discrete":
            result.append(_capability("Alexa.PowerController", ["powerState"]))
        if mode in {"percentage", "hybrid"}:
            range_capability = _capability("Alexa.RangeController", ["rangeValue"]) | {
                "instance": "Blind.Lift",
                "capabilityResources": {
                    "friendlyNames": [
                        {"@type": "asset", "value": {"assetId": "Alexa.Setting.Opening"}}
                    ]
                },
                "configuration": {
                    "supportedRange": {"minimumValue": 0, "maximumValue": 100, "precision": 1}
                },
            }
            if mode == "percentage":
                range_capability["semantics"] = {
                    "actionMappings": [
                        {
                            "@type": "ActionsToDirective",
                            "actions": ["Alexa.Actions.Open"],
                            "directive": {
                                "name": "SetRangeValue",
                                "payload": {"rangeValue": 100},
                            },
                        },
                        {
                            "@type": "ActionsToDirective",
                            "actions": ["Alexa.Actions.Close"],
                            "directive": {
                                "name": "SetRangeValue",
                                "payload": {"rangeValue": 0},
                            },
                        },
                        {
                            "@type": "ActionsToDirective",
                            "actions": ["Alexa.Actions.Raise"],
                            "directive": {
                                "name": "AdjustRangeValue",
                                "payload": {
                                    "rangeValueDelta": 10,
                                    "rangeValueDeltaDefault": False,
                                },
                            },
                        },
                        {
                            "@type": "ActionsToDirective",
                            "actions": ["Alexa.Actions.Lower"],
                            "directive": {
                                "name": "AdjustRangeValue",
                                "payload": {
                                    "rangeValueDelta": -10,
                                    "rangeValueDeltaDefault": False,
                                },
                            },
                        },
                    ]
                }
            result.append(range_capability)
        if mode in {"discrete", "hybrid"}:
            discrete = mode == "discrete"
            open_mode = "Position.Up" if discrete else "position.open"
            close_mode = "Position.Down" if discrete else "position.closed"
            capability_friendly_names = (
                [
                    {"@type": "asset", "value": {"assetId": "Alexa.Setting.Opening"}},
                    {
                        "@type": "text",
                        "value": {"text": "Posizione", "locale": "it-IT"},
                    },
                ]
                if discrete
                else [
                    {
                        "@type": "text",
                        "value": {"text": "Position", "locale": "en-US"},
                    },
                    {"@type": "asset", "value": {"assetId": "Alexa.Setting.Opening"}},
                ]
            )
            supported_modes = [
                {
                    "value": open_mode,
                    "modeResources": {
                        "friendlyNames": [
                            {"@type": "asset", "value": {"assetId": "Alexa.Value.Open"}},
                            {
                                "@type": "text",
                                "value": {"text": "Aperto", "locale": "it-IT"},
                            },
                            {
                                "@type": "text",
                                "value": {"text": "Su", "locale": "it-IT"},
                            },
                        ]
                        if discrete
                        else [
                            {"@type": "asset", "value": {"assetId": "Alexa.Value.Open"}},
                        ]
                    },
                },
                {
                    "value": close_mode,
                    "modeResources": {
                        "friendlyNames": [
                            {"@type": "asset", "value": {"assetId": "Alexa.Value.Close"}},
                            {
                                "@type": "text",
                                "value": {"text": "Chiuso", "locale": "it-IT"},
                            },
                            {
                                "@type": "text",
                                "value": {"text": "Giù", "locale": "it-IT"},
                            },
                        ]
                        if discrete
                        else [
                            {"@type": "asset", "value": {"assetId": "Alexa.Value.Close"}},
                        ]
                    },
                },
            ]
            if not discrete and entity.supported_features & COVER_STOP:
                supported_modes.append(
                    {
                        "value": "position.custom",
                        "modeResources": {
                            "friendlyNames": [
                                {
                                    "@type": "text",
                                    "value": {"text": "Custom", "locale": "en-US"},
                                },
                                {
                                    "@type": "asset",
                                    "value": {"assetId": "Alexa.Setting.Preset"},
                                },
                            ]
                        },
                    }
                )
            result.append(
                _capability("Alexa.ModeController", ["mode"])
                | {
                    "instance": "Blinds.Position" if discrete else "cover.position",
                    "capabilityResources": {"friendlyNames": capability_friendly_names},
                    "configuration": {
                        "ordered": False,
                        "supportedModes": supported_modes,
                    },
                    "semantics": {
                        "actionMappings": [
                            {
                                "@type": "ActionsToDirective",
                                "actions": (
                                    ["Alexa.Actions.Close", "Alexa.Actions.Lower"]
                                    if discrete
                                    else ["Alexa.Actions.Lower", "Alexa.Actions.Close"]
                                ),
                                "directive": {
                                    "name": "SetMode",
                                    "payload": {"mode": close_mode},
                                },
                            },
                            {
                                "@type": "ActionsToDirective",
                                "actions": (
                                    ["Alexa.Actions.Open", "Alexa.Actions.Raise"]
                                    if discrete
                                    else ["Alexa.Actions.Raise", "Alexa.Actions.Open"]
                                ),
                                "directive": {
                                    "name": "SetMode",
                                    "payload": {"mode": open_mode},
                                },
                            },
                        ],
                        "stateMappings": [
                            {
                                "@type": "StatesToValue",
                                "states": ["Alexa.States.Closed"],
                                "value": close_mode,
                            },
                            {
                                "@type": "StatesToValue",
                                "states": ["Alexa.States.Open"],
                                "value": open_mode,
                            },
                        ],
                    },
                }
            )
        if entity.supported_features & COVER_STOP:
            result.append(
                _capability("Alexa.PlaybackController")
                | {"instance": "cover.stop", "supportedOperations": ["Stop"]}
            )
    elif entity.ha_domain == "climate":
        thermostat_modes = _climate_supported_modes(entity)
        if _climate_target_temperature(entity) is not None and thermostat_modes:
            result.append(
                _capability("Alexa.ThermostatController", ["targetSetpoint", "thermostatMode"])
                | {"configuration": {"supportedModes": thermostat_modes}}
            )
        if _numeric_attribute(attributes, "current_temperature") is not None:
            result.append(_capability("Alexa.TemperatureSensor", ["temperature"]))
    elif entity.ha_domain == "fan":
        result.append(_capability("Alexa.PercentageController", ["percentage"]))
    elif entity.ha_domain == "scene":
        result.append(
            {
                "type": "AlexaInterface",
                "interface": "Alexa.SceneController",
                "version": "3",
                "supportsDeactivation": False,
                "proactivelyReported": False,
            }
        )
    return result


def endpoint_id(entity: Entity) -> str:
    return f"ev1_{entity.id.hex}"


def _cover_display_category(entity: Entity) -> str:
    device_class = entity.device_class
    if not isinstance(device_class, str):
        return "OTHER"
    return {
        "garage": "GARAGE_DOOR",
        "gate": "GARAGE_DOOR",
        "door": "DOOR",
        "blind": "INTERIOR_BLIND",
        "shade": "INTERIOR_BLIND",
        "curtain": "INTERIOR_BLIND",
        "window": "EXTERIOR_BLIND",
        "awning": "EXTERIOR_BLIND",
        "shutter": "EXTERIOR_BLIND",
    }.get(device_class, "OTHER")


def discovery_endpoint(entity: Entity) -> dict[str, Any]:
    category = {
        "light": "LIGHT",
        "switch": "SWITCH",
        "cover": _cover_display_category(entity),
        "climate": "THERMOSTAT",
        "fan": "FAN",
        "scene": "SCENE_TRIGGER",
    }[entity.ha_domain]
    return {
        "endpointId": endpoint_id(entity),
        "manufacturerName": "Ekonex",
        "friendlyName": effective_voice_name(entity),
        "description": "Home Assistant entity via Ekonex Voice",
        "displayCategories": [category],
        "additionalAttributes": {"manufacturer": "Ekonex", "model": "Ekonex Voice"},
        "cookie": {},
        "capabilities": capabilities(entity),
    }


def _property(
    namespace: str, name: str, value: Any, *, instance: str | None = None
) -> dict[str, Any]:
    item = {
        "namespace": namespace,
        "name": name,
        "value": value,
        "timeOfSample": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "uncertaintyInMilliseconds": 1000,
    }
    if instance:
        item["instance"] = instance
    return item


def _numeric_attribute(attributes: dict[str, Any], name: str) -> int | float | None:
    value = attributes.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _climate_target_temperature(entity: Entity) -> int | float | None:
    attributes = entity.attributes_json or {}
    target = _numeric_attribute(attributes, "temperature")
    return target if target is not None else _numeric_attribute(attributes, "target_temp")


def _climate_temperature_scale(entity: Entity) -> str:
    attributes = entity.attributes_json or {}
    unit = attributes.get("temperature_unit", attributes.get("unit_of_measurement"))
    if isinstance(unit, str):
        normalized = unit.strip().casefold()
        if normalized in {"°f", "f", "fahrenheit"}:
            return "FAHRENHEIT"
        if normalized in {"k", "kelvin"}:
            return "KELVIN"
    return "CELSIUS"


def _climate_hvac_modes(entity: Entity) -> list[str]:
    modes = (entity.attributes_json or {}).get("hvac_modes")
    if not isinstance(modes, list):
        return []
    return [mode for mode in modes if isinstance(mode, str)]


def _climate_supported_modes(entity: Entity) -> list[str]:
    supported: list[str] = []
    for ha_mode in _climate_hvac_modes(entity):
        alexa_mode = HA_TO_ALEXA_THERMOSTAT_MODE.get(ha_mode)
        if alexa_mode is not None and alexa_mode not in supported:
            supported.append(alexa_mode)
    # Amazon requires every ThermostatController to support HEAT or COOL.
    return supported if {"HEAT", "COOL"} & set(supported) else []


def _ha_mode_for_alexa(entity: Entity, alexa_mode: str) -> str | None:
    return next(
        (
            ha_mode
            for ha_mode in _climate_hvac_modes(entity)
            if HA_TO_ALEXA_THERMOSTAT_MODE.get(ha_mode) == alexa_mode
        ),
        None,
    )


def state_properties(entity: Entity) -> list[dict[str, Any]]:
    attributes = entity.attributes_json or {}
    props = [
        _property(
            "Alexa.EndpointHealth",
            "connectivity",
            {"value": "OK" if entity.available else "UNREACHABLE"},
        )
    ]
    if entity.ha_domain in {"light", "switch", "fan"}:
        props.append(
            _property(
                "Alexa.PowerController", "powerState", "ON" if entity.state == "on" else "OFF"
            )
        )
    elif entity.ha_domain == "cover" and effective_cover_mode(entity) == "discrete":
        props.append(
            _property(
                "Alexa.PowerController",
                "powerState",
                "OFF" if entity.state == "off" else "ON",
            )
        )
    brightness = _numeric_attribute(attributes, "brightness")
    if entity.ha_domain == "light" and brightness is not None:
        props.append(
            _property(
                "Alexa.BrightnessController",
                "brightness",
                round(brightness * 100 / 255),
            )
        )
    if entity.ha_domain == "light" and "rgb_color" in attributes:
        rgb = attributes["rgb_color"]
        if (
            isinstance(rgb, list)
            and len(rgb) == 3
            and all(
                not isinstance(component, bool)
                and isinstance(component, (int, float))
                and math.isfinite(component)
                for component in rgb
            )
        ):
            import colorsys

            hue, saturation, brightness = colorsys.rgb_to_hsv(
                rgb[0] / 255, rgb[1] / 255, rgb[2] / 255
            )
            props.append(
                _property(
                    "Alexa.ColorController",
                    "color",
                    {
                        "hue": round(hue * 360, 3),
                        "saturation": round(saturation, 4),
                        "brightness": round(brightness, 4),
                    },
                )
            )
    color_temperature = _numeric_attribute(attributes, "color_temp_kelvin")
    if entity.ha_domain == "light" and color_temperature is not None:
        props.append(
            _property(
                "Alexa.ColorTemperatureController",
                "colorTemperatureInKelvin",
                color_temperature,
            )
        )
    if entity.ha_domain == "cover":
        mode = "percentage" if _is_office_range_ab(entity) else effective_cover_mode(entity)
        current_position = _numeric_attribute(attributes, "current_position")
        discrete_position = (
            "Position.Up"
            if entity.state == "open"
            else "Position.Down"
            if entity.state == "closed"
            else None
        )
        if mode in {"percentage", "hybrid"} and current_position is not None:
            props.append(
                _property(
                    "Alexa.RangeController",
                    "rangeValue",
                    current_position,
                    instance="cover.position" if _is_office_range_ab(entity) else "Blind.Lift",
                )
            )
        if mode == "discrete" and discrete_position is not None:
            props.append(
                _property(
                    "Alexa.ModeController",
                    "mode",
                    discrete_position,
                    instance="Blinds.Position",
                )
            )
        elif mode == "hybrid" and discrete_position is not None:
            props.append(
                _property(
                    "Alexa.ModeController",
                    "mode",
                    "position.open" if entity.state == "open" else "position.closed",
                    instance="cover.position",
                )
            )
    if entity.ha_domain == "fan":
        percentage = _numeric_attribute(attributes, "percentage")
        if percentage is not None:
            props.append(
                _property(
                    "Alexa.PercentageController",
                    "percentage",
                    percentage,
                )
            )
    if entity.ha_domain == "climate":
        supported_modes = _climate_supported_modes(entity)
        target_temperature = _climate_target_temperature(entity)
        scale = _climate_temperature_scale(entity)
        if target_temperature is not None and supported_modes:
            props.append(
                _property(
                    "Alexa.ThermostatController",
                    "targetSetpoint",
                    {"value": target_temperature, "scale": scale},
                )
            )
            thermostat_mode = HA_TO_ALEXA_THERMOSTAT_MODE.get(str(entity.state))
            if thermostat_mode in supported_modes:
                props.append(
                    _property("Alexa.ThermostatController", "thermostatMode", thermostat_mode)
                )
        current_temperature = _numeric_attribute(attributes, "current_temperature")
        if current_temperature is not None:
            props.append(
                _property(
                    "Alexa.TemperatureSensor",
                    "temperature",
                    {"value": current_temperature, "scale": scale},
                )
            )
    return props


def _command(
    namespace: str, name: str, payload: dict[str, Any], entity: Entity | None = None
) -> dict[str, object] | None:
    mapping: dict[tuple[str, str], dict[str, object]] = {
        ("Alexa.PowerController", "TurnOn"): {"operation": "power_on"},
        ("Alexa.PowerController", "TurnOff"): {"operation": "power_off"},
        ("Alexa.SceneController", "Activate"): {"operation": "activate"},
    }
    if (namespace, name) in mapping:
        if entity is not None and entity.ha_domain == "cover":
            if effective_cover_mode(entity) != "discrete":
                return None
            if (namespace, name) == ("Alexa.PowerController", "TurnOn"):
                return {"operation": "open"}
            if (namespace, name) == ("Alexa.PowerController", "TurnOff"):
                return {"operation": "close"}
            return None
        return mapping[(namespace, name)]
    if namespace == "Alexa.BrightnessController" and name == "SetBrightness":
        return {
            "operation": "set_brightness",
            "brightness": round(float(payload["brightness"]) * 255 / 100),
        }
    if namespace == "Alexa.ColorController" and name == "SetColor":
        color = payload["color"]
        return {
            "operation": "set_color",
            "rgb_color": _hsv_rgb(
                float(color["hue"]), float(color["saturation"]), float(color["brightness"])
            ),
        }
    if namespace == "Alexa.ColorTemperatureController" and name == "SetColorTemperature":
        return {
            "operation": "set_color_temperature",
            "color_temp_kelvin": float(payload["colorTemperatureInKelvin"]),
        }
    if namespace == "Alexa.RangeController" and name == "SetRangeValue":
        if entity is not None and _is_office_range_ab(entity):
            range_value = float(payload["rangeValue"])
            if range_value == 100:
                return {"operation": "open"}
            if range_value == 0:
                return {"operation": "close"}
            return {"operation": "set_position", "position": round(range_value)}
        if entity is None or effective_cover_mode(entity) not in {"percentage", "hybrid"}:
            return None
        return {"operation": "set_position", "position": round(float(payload["rangeValue"]))}
    if namespace == "Alexa.RangeController" and name == "AdjustRangeValue" and entity is not None:
        if _is_office_range_ab(entity):
            return None
        if effective_cover_mode(entity) not in {"percentage", "hybrid"}:
            return None
        current = float((entity.attributes_json or {}).get("current_position", 0))
        return {
            "operation": "set_position",
            "position": round(min(100, max(0, current + float(payload["rangeValueDelta"])))),
        }
    if namespace == "Alexa.ModeController" and name == "SetMode":
        if (
            entity is None
            or _is_office_range_ab(entity)
            or effective_cover_mode(entity) not in {"discrete", "hybrid"}
        ):
            return None
        mode_value = payload.get("mode")
        mode_mapping = (
            {
                "Position.Up": "open",
                "Position.Down": "close",
                "position.open": "open",
                "position.closed": "close",
                "position.custom": "stop" if entity.supported_features & COVER_STOP else None,
            }
            if effective_cover_mode(entity) == "discrete"
            else {
                "position.open": "open",
                "position.closed": "close",
                "position.custom": "stop" if entity.supported_features & COVER_STOP else None,
            }
        )
        operation = mode_mapping.get(mode_value) if isinstance(mode_value, str) else None
        return {"operation": operation} if operation is not None else None
    if namespace == "Alexa.PlaybackController" and name in {"Pause", "Stop"}:
        if entity is None or entity.ha_domain != "cover":
            return None
        return {"operation": "stop"} if entity.supported_features & COVER_STOP else None
    if namespace == "Alexa.PercentageController" and name == "SetPercentage":
        return {"operation": "set_percentage", "percentage": round(float(payload["percentage"]))}
    if namespace == "Alexa.ThermostatController" and name == "SetTargetTemperature":
        if (
            entity is None
            or entity.ha_domain != "climate"
            or not _climate_supported_modes(entity)
            or _climate_target_temperature(entity) is None
        ):
            return None
        target = payload.get("targetSetpoint")
        if not isinstance(target, dict):
            return None
        value = target.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        temperature = float(value)
        if not math.isfinite(temperature):
            return None
        return {
            "operation": "set_target_temperature",
            "temperature": temperature,
        }
    if namespace == "Alexa.ThermostatController" and name == "SetThermostatMode":
        if entity is None or entity.ha_domain != "climate":
            return None
        requested = payload.get("thermostatMode")
        if isinstance(requested, dict):
            requested = requested.get("value")
        if not isinstance(requested, str) or requested not in _climate_supported_modes(entity):
            return None
        ha_mode = _ha_mode_for_alexa(entity, requested)
        return {"operation": "set_hvac_mode", "hvac_mode": ha_mode} if ha_mode else None
    return None


def _hsv_rgb(hue: float, saturation: float, brightness: float) -> tuple[int, int, int]:
    import colorsys

    return tuple(
        round(value * 255) for value in colorsys.hsv_to_rgb(hue / 360, saturation, brightness)
    )  # type: ignore[return-value]


def _event(
    header: dict[str, Any],
    payload: dict[str, Any],
    endpoint: dict[str, str] | None = None,
    properties: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {"header": header, "payload": payload}
    if endpoint is not None:
        event["endpoint"] = endpoint
    result: dict[str, Any] = {"event": event}
    if properties is not None:
        result["context"] = {"properties": properties}
    return result


_SENSITIVE_DIAGNOSTIC_KEYS = {
    "authorization",
    "access_token",
    "refresh_token",
    "token",
    "secret",
    "password",
    "cookie",
    "scope",
}


def _diagnostic_payload(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): _diagnostic_payload(item)
            for key, item in value.items()
            if not _sensitive_diagnostic_key(str(key))
        }
    if isinstance(value, list):
        return [_diagnostic_payload(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _sensitive_diagnostic_key(key: str) -> bool:
    normalized = key.casefold()
    return normalized in _SENSITIVE_DIAGNOSTIC_KEYS or any(
        fragment in normalized
        for fragment in ("token", "secret", "password", "authorization", "cookie")
    )


def _alexa_audit(
    database: AsyncSession,
    *,
    tenant_id: UUID,
    event_type: str,
    correlation_id: UUID,
    amazon_message_id: str,
    payload: dict[str, object],
    result: str,
    installation_id: UUID | None = None,
) -> None:
    database.add(
        AuditEvent(
            tenant_id=tenant_id,
            installation_id=installation_id,
            source="alexa",
            event_type=event_type,
            request_id=amazon_message_id,
            payload_redacted_json={
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "correlation_id": str(correlation_id),
                "amazon_message_id": amazon_message_id,
                **payload,
            },
            result=result,
        )
    )


def _mapping_rule(namespace: str, name: str, payload: dict[str, Any]) -> str:
    if namespace == "Alexa.ModeController" and name == "SetMode":
        return f"ModeController.SetMode({payload.get('mode')})"
    if namespace == "Alexa.PlaybackController":
        return f"PlaybackController.{name}"
    if namespace == "Alexa.PowerController":
        return f"PowerController.{name}"
    return f"{namespace}.{name}"


@router.post("/alexa/v1/directive")
async def directive(request: Request, database: AsyncSession = database_dependency) -> JSONResponse:
    raw = await request.body()
    if len(raw) > MAX_DIRECTIVE_BYTES:
        raise HTTPException(413, "directive_too_large")
    try:
        body = json.loads(raw)
        directive = body["directive"]
        header = directive["header"]
        if header["payloadVersion"] != "3" or not all(
            isinstance(header[key], str) for key in ("namespace", "name", "messageId")
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "INVALID_DIRECTIVE") from exc
    message_id = header["messageId"]
    if header["namespace"] == "Alexa.Authorization" and header["name"] == "AcceptGrant":
        payload = directive.get("payload", {})
        link = await _authenticate(payload.get("grantee", {}).get("token", ""), database)
        from .alexa_events import AlexaEventGateway

        gateway = AlexaEventGateway(database)
        try:
            await gateway.accept_grant(link, str(payload.get("grant", {}).get("code", "")))
        finally:
            await gateway.close()
        return JSONResponse(
            _event(
                {
                    "namespace": "Alexa.Authorization",
                    "name": "AcceptGrant.Response",
                    "payloadVersion": "3",
                    "messageId": str(uuid4()),
                },
                {},
            )
        )
    scope = directive.get("endpoint", {}).get("scope") or directive.get("payload", {}).get(
        "scope", {}
    )
    link = await _authenticate(scope.get("token", ""), database)
    replay_key = f"{link.id}:{message_id}"
    if replay_key in _replay:
        return JSONResponse(_replay[replay_key])
    correlation = header.get("correlationToken")
    diagnostic_correlation_id: UUID | None = None
    if not (header["namespace"] == "Alexa.Discovery" and header["name"] == "Discover"):
        diagnostic_correlation_id = uuid4()
        endpoint_value = str(directive.get("endpoint", {}).get("endpointId", ""))
        _alexa_audit(
            database,
            tenant_id=link.tenant_id,
            event_type="alexa.directive_received",
            correlation_id=diagnostic_correlation_id,
            amazon_message_id=message_id,
            payload={
                "endpoint_id": endpoint_value,
                "namespace": header["namespace"],
                "name": header["name"],
                "instance": header.get("instance"),
                "payload": _diagnostic_payload(directive.get("payload", {})),
                "tenant_id": str(link.tenant_id),
                "installation_id": None,
            },
            result="received",
        )
        await database.commit()
    if header["namespace"] == "Alexa.Discovery" and header["name"] == "Discover":
        installations = list(
            (
                await database.scalars(
                    select(Installation).where(Installation.tenant_id == link.tenant_id)
                )
            ).all()
        )
        entities = list(
            (
                await database.scalars(
                    select(Entity)
                    .join(Installation)
                    .where(
                        Installation.tenant_id == link.tenant_id,
                        Entity.deleted_at.is_(None),
                        Entity.ha_domain.in_(SUPPORTED_DOMAINS),
                    )
                )
            ).all()
        )
        entities = unambiguous_voice_entities(
            [entity for entity in entities if alexa_entity_eligible(entity)]
        )
        published = [(entity, discovery_endpoint(entity)) for entity in entities]
        response = _event(
            {
                "namespace": "Alexa.Discovery",
                "name": "Discover.Response",
                "payloadVersion": "3",
                "messageId": str(uuid4()),
            },
            {"endpoints": [endpoint for _, endpoint in published]},
        )
        try:
            await record_discovery(database, link.tenant_id, link.id, installations, published)
        except SQLAlchemyError:
            await database.rollback()
            logger.exception(
                "Failed to record Alexa Discovery observation tenant_id=%s", link.tenant_id
            )
    else:
        endpoint = directive.get("endpoint", {})
        endpoint_value = endpoint.get("endpointId", "")
        if not endpoint_value.startswith("ev1_"):
            raise HTTPException(400, "NO_SUCH_ENDPOINT")
        try:
            entity_uuid = UUID(hex=endpoint_value[4:])
        except ValueError as exc:
            raise HTTPException(400, "NO_SUCH_ENDPOINT") from exc
        entity_query = (
            select(Entity)
            .join(Installation)
            .where(
                Installation.tenant_id == link.tenant_id,
                Entity.deleted_at.is_(None),
            )
        )
        entity_query = entity_query.where(Entity.id == entity_uuid)
        entity = await database.scalar(entity_query)
        if entity is None or entity.ha_registry_id is None or not alexa_entity_eligible(entity):
            if diagnostic_correlation_id is not None:
                _alexa_audit(
                    database,
                    tenant_id=link.tenant_id,
                    event_type="alexa.endpoint_resolved",
                    correlation_id=diagnostic_correlation_id,
                    amazon_message_id=message_id,
                    payload={"endpoint_id": endpoint_value, "failure_reason": "NO_SUCH_ENDPOINT"},
                    result="failure",
                )
                await database.commit()
            raise HTTPException(404, "NO_SUCH_ENDPOINT")
        if diagnostic_correlation_id is not None:
            published_endpoint = discovery_endpoint(entity)
            _alexa_audit(
                database,
                tenant_id=link.tenant_id,
                installation_id=entity.installation_id,
                event_type="alexa.endpoint_resolved",
                correlation_id=diagnostic_correlation_id,
                amazon_message_id=message_id,
                payload={
                    "endpoint_id": endpoint_value,
                    "entity_id": str(entity.id),
                    "ha_entity_id": entity.ha_entity_id,
                    "voice_name": effective_voice_name(entity),
                    "friendly_name": entity.friendly_name,
                    "ha_domain": entity.ha_domain,
                    "device_class": entity.device_class,
                    "display_categories": published_endpoint["displayCategories"],
                    "capabilities": [
                        {
                            "interface": capability["interface"],
                            "instance": capability.get("instance"),
                        }
                        for capability in published_endpoint["capabilities"]
                    ],
                    "supported_features": entity.supported_features,
                    "state": entity.state,
                    "available": entity.available,
                },
                result="success",
            )
        if header["namespace"] == "Alexa" and header["name"] == "ReportState":
            response = _event(
                {
                    "namespace": "Alexa",
                    "name": "StateReport",
                    "payloadVersion": "3",
                    "messageId": str(uuid4()),
                    "correlationToken": correlation,
                },
                {},
                {"endpointId": endpoint_value},
                state_properties(entity),
            )
        else:
            command_payload = directive.get("payload", {})
            if entity.ha_domain == "cover":
                logger.info(
                    "alexa_directive_received %s",
                    {
                        "namespace": header["namespace"],
                        "name": header["name"],
                        "instance": header.get("instance"),
                        "payload_mode": (
                            command_payload.get("mode")
                            if isinstance(command_payload, dict)
                            else None
                        ),
                        "endpoint_id": endpoint_value,
                    },
                )
            spec = _command(header["namespace"], header["name"], command_payload, entity)
            if diagnostic_correlation_id is not None:
                _alexa_audit(
                    database,
                    tenant_id=link.tenant_id,
                    installation_id=entity.installation_id,
                    event_type="alexa.mapping_result",
                    correlation_id=diagnostic_correlation_id,
                    amazon_message_id=message_id,
                    payload={
                        "endpoint_id": endpoint_value,
                        "entity_id": str(entity.id),
                        "ha_entity_id": entity.ha_entity_id,
                        "namespace": header["namespace"],
                        "name": header["name"],
                        "instance": header.get("instance"),
                        "payload": _diagnostic_payload(command_payload),
                        "operation": spec.get("operation") if spec else None,
                        "normalized_payload": spec,
                        "mapping_rule": _mapping_rule(
                            header["namespace"], header["name"], command_payload
                        ),
                    },
                    result="success" if spec else "failure",
                )
                await database.commit()
            advertised = {cap["interface"] for cap in capabilities(entity)}
            if spec is None or header["namespace"] not in advertised:
                response = _event(
                    {
                        "namespace": "Alexa",
                        "name": "ErrorResponse",
                        "payloadVersion": "3",
                        "messageId": str(uuid4()),
                        "correlationToken": correlation,
                    },
                    {"type": "INVALID_DIRECTIVE", "message": "INVALID_DIRECTIVE"},
                    {"endpointId": endpoint_value},
                )
            else:
                command = command_adapter.validate_python(spec)
                outcome = await CommandDispatchService(database, sessions).dispatch(
                    entity.installation_id,
                    entity.ha_registry_id,
                    command,
                    command_id=UUID(message_id) if _is_uuid(message_id) else uuid4(),
                    correlation_id=diagnostic_correlation_id,
                )
                if outcome.status != "success":
                    error_type = {
                        "unavailable": "ENDPOINT_UNREACHABLE",
                        "timeout": "ENDPOINT_UNREACHABLE",
                        "invalid_argument": "INVALID_VALUE",
                        "unsupported_command": "INVALID_DIRECTIVE",
                    }.get(outcome.status, "INTERNAL_ERROR")
                    response = _event(
                        {
                            "namespace": "Alexa",
                            "name": "ErrorResponse",
                            "payloadVersion": "3",
                            "messageId": str(uuid4()),
                            "correlationToken": correlation,
                        },
                        {"type": error_type, "message": error_type},
                        {"endpointId": endpoint_value},
                    )
                else:
                    response = _event(
                        {
                            "namespace": "Alexa",
                            "name": "Response",
                            "payloadVersion": "3",
                            "messageId": str(uuid4()),
                            "correlationToken": correlation,
                        },
                        {},
                        {"endpointId": endpoint_value},
                        state_properties(entity),
                    )
    if diagnostic_correlation_id is not None:
        response_event = response["event"]
        response_header = response_event["header"]
        response_payload = response_event.get("payload", {})
        response_success = response_header.get("name") not in {"ErrorResponse"}
        _alexa_audit(
            database,
            tenant_id=link.tenant_id,
            event_type="alexa.response_generated",
            correlation_id=diagnostic_correlation_id,
            amazon_message_id=message_id,
            installation_id=entity.installation_id if "entity" in locals() and entity else None,
            payload={
                "response_namespace": response_header.get("namespace"),
                "response_name": response_header.get("name"),
                "success": response_success,
                "error_type": response_payload.get("type"),
                "error_message": response_payload.get("message"),
                "context": _diagnostic_payload(response.get("context", {})),
            },
            result="success" if response_success else "failure",
        )
        await database.commit()
    _replay[replay_key] = response
    if len(_replay) > 2048:
        _replay.pop(next(iter(_replay)))
    return JSONResponse(response)


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except ValueError:
        return False
