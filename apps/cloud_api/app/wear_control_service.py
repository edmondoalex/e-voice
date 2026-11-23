"""Experimental dedicated Wear control service. Disabled until explicitly qualified."""

import hashlib
import hmac
import json
import re
import time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .database import get_database_session
from .domain.models import Installation
from .wear_bridge import WearBridgeAuthority, WearBridgeNonce, canonical, verify_service_key
from .wear_cutover import (
    CutoverBlocked,
    installation_snapshot,
    restore_legacy,
    transfer_legacy_to_econtrol,
)

router = APIRouter(tags=["wear-control-staged"])
session_dependency = Depends(get_database_session)


@router.post("/service/wear/control/{operation}")
async def control(operation: str, request: Request, session: AsyncSession = session_dependency):
    settings = get_settings()
    provider = getattr(request.app.state, "wear_bridge_key_provider", None)
    if not (
        settings.wear_bridge_fence_enabled
        and getattr(settings, "wear_bridge_control_enabled", False)
        and provider
    ):
        raise HTTPException(503, "wear_control_disabled")
    if operation not in {"snapshot", "cutover", "rollback", "authority", "capabilities", "mapping"}:
        raise HTTPException(404, "unknown_operation")
    raw = await request.body()
    if len(raw) > 8192:
        raise HTTPException(413, "request_too_large")
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {"claims", "body", "signature"}:
            raise ValueError()
        claims, body, sig = data["claims"], data["body"], data["signature"]
        if (
            not isinstance(claims, dict)
            or set(claims)
            != {
                "issuer",
                "audience",
                "installation",
                "scope",
                "method",
                "path",
                "issued",
                "expires",
                "nonce",
            }
            or not isinstance(body, dict)
        ):
            raise ValueError()
        installation = UUID(claims["installation"])
        now = int(time.time())
        path = "/service/wear/control/" + operation
        if (
            claims["issuer"] != "econtrol"
            or claims["audience"] != "evoice-wear-service"
            or claims["scope"] != "wear.control." + operation
            or claims["method"] != "POST"
            or claims["path"] != path
            or type(claims["issued"]) is not int
            or type(claims["expires"]) is not int
            or claims["issued"] > now + 30
            or claims["expires"] <= now
            or not 0 < claims["expires"] - claims["issued"] <= 60
            or not isinstance(claims["nonce"], str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{24,128}", claims["nonce"])
        ):
            raise ValueError()
        key = verify_service_key(await provider(installation), sig, canonical(claims, body))
        if operation in {"snapshot", "authority", "capabilities"} and body != {}:
            raise ValueError()
        if operation in {"cutover", "rollback"} and (
            set(body) != {"generation", "revision", "proof"}
            or type(body["generation"]) is not int
            or not isinstance(body["revision"], str)
            or not isinstance(body["proof"], str)
        ):
            raise ValueError()
        if operation == "mapping":
            if (
                set(body) != {"generation", "revision", "entity_ids"}
                or type(body["generation"]) is not int
                or not isinstance(body["revision"], str)
                or not isinstance(body["entity_ids"], list)
                or not 0 <= len(body["entity_ids"]) <= 128
            ):
                raise ValueError()
            ids = [UUID(v) for v in body["entity_ids"]]
            if len(set(ids)) != len(ids) or any(
                str(i) != v for i, v in zip(ids, body["entity_ids"], strict=True)
            ):
                raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(403, "wear_control_denied") from None
    # Same order as revoke/receiver; the nonce insert references Installation.
    instance = await session.scalar(
        select(Installation)
        .where(Installation.id == installation)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if instance is None:
        await session.rollback()
        raise HTTPException(409, "wear_control_authority")
    authority = await session.scalar(
        select(WearBridgeAuthority)
        .where(WearBridgeAuthority.installation_id == installation)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if claims["expires"] <= int(time.time()):
        await session.rollback()
        raise HTTPException(403, "wear_control_expired")
    if authority is None:
        await session.rollback()
        raise HTTPException(409, "wear_control_authority")
    await session.execute(
        delete(WearBridgeNonce).where(
            WearBridgeNonce.installation_id == installation,
            WearBridgeNonce.expires <= int(time.time()),
        )
    )
    if await session.get(WearBridgeNonce, (installation, claims["nonce"])) is not None:
        await session.rollback()
        raise HTTPException(403, "wear_control_replay")
    session.add(
        WearBridgeNonce(
            installation_id=installation, nonce=claims["nonce"], expires=claims["expires"]
        )
    )
    try:
        if operation == "snapshot":
            if authority.owner != "legacy":
                raise CutoverBlocked("stale_authority")
            snapshot, revision = await installation_snapshot(session, installation)
            issued = int(time.time())
            export_claims = {
                "issuer": "evoice",
                "audience": "econtrol",
                "scope": "wear.snapshot.full",
                "installation": str(installation),
                "generation": authority.generation,
                "revision": revision,
                "issued": issued,
                "expires": issued + 60,
            }
            export = {"claims": export_claims, "snapshot": snapshot}
            encoded = json.dumps(
                export, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            ).encode()
            export["signature"] = hmac.new(
                key, b"ekonex-wear-snapshot-v1\0" + encoded, hashlib.sha256
            ).hexdigest()
            await session.commit()
            return export
        if operation == "mapping":
            if (authority.owner, authority.generation, authority.import_revision) != (
                "econtrol",
                body["generation"],
                body["revision"],
            ):
                raise CutoverBlocked("stale_authority")
            from .domain.models import Entity

            entities = (
                await session.scalars(
                    select(Entity)
                    .where(
                        Entity.installation_id == installation,
                        Entity.id.in_(ids),
                        Entity.deleted_at.is_(None),
                    )
                    .execution_options(populate_existing=True)
                )
            ).all()
            if len(entities) != len(ids):
                raise CutoverBlocked("mapping_unavailable")
            mapping = {
                str(e.id): {"entity_id": e.ha_entity_id, "registry_id": e.ha_registry_id}
                for e in entities
            }
            current_installation = await session.scalar(
                select(Installation)
                .where(Installation.id == installation)
                .execution_options(populate_existing=True)
            )
            if current_installation is None:
                raise CutoverBlocked("mapping_unavailable")
            issued = int(time.time())
            statement = {
                "installation": str(installation),
                "generation": authority.generation,
                "revision": authority.import_revision,
                "mapping_revision": current_installation.sync_revision,
                "nonce": claims["nonce"],
                "issued": issued,
                "expires": issued + 15,
                "mapping": mapping,
            }
            raw = json.dumps(
                statement,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
            receipt = hmac.new(key, b"ekonex-wear-mapping-v1\0" + raw, hashlib.sha256).hexdigest()
            await session.commit()
            return {"statement": statement, "receipt": receipt}
        if operation == "cutover":
            generation = await transfer_legacy_to_econtrol(
                session,
                installation,
                body["generation"],
                body["revision"],
                body["revision"],
                body["proof"],
                key,
                qualified=True,
            )
        elif operation == "rollback":
            generation = await restore_legacy(
                session,
                installation,
                body["generation"],
                body["revision"],
                body["proof"],
                key,
                qualified=True,
            )
        else:
            generation = authority.generation
        await session.commit()
        # Issued only after commit; query again in a separate transaction below.
        refreshed = await session.scalar(
            select(WearBridgeAuthority)
            .where(WearBridgeAuthority.installation_id == installation)
            .execution_options(populate_existing=True)
        )
        owner, revision, generation = (
            refreshed.owner,
            refreshed.import_revision,
            refreshed.generation,
        )
        if operation == "capabilities":
            issued = int(time.time())
            statement = {
                "installation": str(installation),
                "owner": owner,
                "generation": generation,
                "revision": revision,
                "nonce": claims["nonce"],
                "issued": issued,
                "expires": issued + 15,
                "render_v2": getattr(settings, "wear_render_v2_enabled", False) is True,
            }
            signed = json.dumps(
                statement,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
            return {
                "statement": statement,
                "receipt": hmac.new(
                    key, b"ekonex-wear-capabilities-v1\0" + signed, hashlib.sha256
                ).hexdigest(),
            }
        if operation == "authority":
            issued = int(time.time())
            statement = {
                "installation": str(installation),
                "owner": owner,
                "generation": generation,
                "revision": revision,
                "nonce": claims["nonce"],
                "issued": issued,
                "expires": issued + 15,
            }
            signed = json.dumps(
                statement,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
            return {
                "statement": statement,
                "receipt": hmac.new(
                    key, b"ekonex-wear-current-authority-v1\0" + signed, hashlib.sha256
                ).hexdigest(),
            }
        if owner == "econtrol" and revision:
            material = (
                b"ekonex-wear-cutover-v1\0"
                + installation.bytes
                + generation.to_bytes(8, "big")
                + revision.encode()
            )
        else:
            material = (
                b"ekonex-wear-authority-v1\0"
                + installation.bytes
                + generation.to_bytes(8, "big")
                + owner.encode()
                + b"\0"
                + (revision or "").encode()
            )
        return {
            "installation": str(installation),
            "owner": owner,
            "generation": generation,
            "revision": revision,
            "receipt": hmac.new(key, material, hashlib.sha256).hexdigest(),
        }
    except CutoverBlocked as exc:
        await session.rollback()
        raise HTTPException(409, str(exc)) from None
