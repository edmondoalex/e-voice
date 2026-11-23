"""Offline-tested authority CAS primitive. No HTTP route or automatic cutover."""

import hashlib
import hmac
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .domain.models import Entity, WearDevice
from .wear_bridge import WearBridgeAuthority, WearBridgeWake


class CutoverBlocked(ValueError):
    pass


async def installation_snapshot(
    session: AsyncSession, installation: UUID
) -> tuple[dict[str, object], str]:
    """Full installation snapshot; caller must hold the authority row lock."""
    devices = (
        await session.scalars(
            select(WearDevice)
            .where(WearDevice.installation_id == installation, WearDevice.revoked_at.is_(None))
            .order_by(WearDevice.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    selected: list[dict[str, object]] = []
    requested: set[UUID] = set()
    for device in devices:
        prefs = device.notification_preferences_json or {}
        rules, actions = prefs.get("notification_rules", []), prefs.get("quick_actions", [])
        if not isinstance(rules, list) or not isinstance(actions, list):
            raise CutoverBlocked("invalid_preferences")
        selected.append(
            {
                "device_id": str(device.id),
                "user_id": str(device.user_id),
                "name": device.display_name,
                "notification_rules": rules,
                "quick_actions": actions,
            }
        )
        for item in rules + actions:
            if isinstance(item, dict) and item.get("entity_id"):
                try:
                    requested.add(UUID(str(item["entity_id"])))
                except ValueError:
                    raise CutoverBlocked("invalid_entity_reference") from None
    entities = (
        (
            await session.scalars(
                select(Entity)
                .where(
                    Entity.id.in_(requested),
                    Entity.installation_id == installation,
                    Entity.deleted_at.is_(None),
                )
                .execution_options(populate_existing=True)
            )
        ).all()
        if requested
        else []
    )
    mapping = {
        str(e.id): {
            "registry_id": e.ha_registry_id,
            "entity_id": e.ha_entity_id,
            "display_name": e.voice_name or e.display_name or e.friendly_name,
        }
        for e in entities
    }
    if set(map(str, requested)) != set(mapping):
        raise CutoverBlocked("unmapped_entity")
    snapshot: dict[str, object] = {"devices": selected, "entity_mapping": mapping}
    raw = json.dumps(
        snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()
    return snapshot, hashlib.sha256(raw).hexdigest()


def import_proof(key: bytes, installation: UUID, generation: int, revision: str) -> str:
    from .wear_contract_v1 import import_proof as contract_proof

    try:
        return contract_proof(key, installation, generation, revision)
    except ValueError as exc:
        raise CutoverBlocked(str(exc)) from None


async def transfer_legacy_to_econtrol(
    session: AsyncSession,
    installation: UUID,
    generation: int,
    source_revision: str,
    imported_revision: str,
    proof: str,
    key: bytes,
    *,
    qualified: bool = False,
) -> int:
    """CAS within caller transaction; caller must verify a fresh full installation snapshot.

    `qualified` must only follow full cutover, writer-fence, worker and key tests.
    No application route currently sets it.
    """
    if qualified is not True:
        raise CutoverBlocked("cutover_not_qualified")
    expected = import_proof(key, installation, generation, source_revision)
    if (
        source_revision != imported_revision
        or not isinstance(proof, str)
        or not hmac.compare_digest(expected, proof)
    ):
        raise CutoverBlocked("import_unverified")
    authority = await session.scalar(
        select(WearBridgeAuthority)
        .where(WearBridgeAuthority.installation_id == installation)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if authority is None:
        raise CutoverBlocked("missing_authority")
    if (
        authority.owner == "econtrol"
        and authority.generation == generation + 1
        and authority.import_revision == source_revision
    ):
        return authority.generation
    if authority.owner != "legacy" or authority.generation != generation:
        raise CutoverBlocked("stale_authority")
    _snapshot, current_revision = await installation_snapshot(session, installation)
    if current_revision != source_revision:
        raise CutoverBlocked("snapshot_changed")
    pending = await session.scalar(
        select(WearBridgeWake.device_id)
        .join(WearDevice, WearDevice.id == WearBridgeWake.device_id)
        .where(WearDevice.installation_id == installation)
        .limit(1)
    )
    if pending is not None:
        raise CutoverBlocked("pending_wake")
    authority.owner = "econtrol"
    authority.generation = generation + 1
    authority.import_revision = source_revision
    await session.flush()
    return authority.generation


def drain_proof(key: bytes, installation: UUID, generation: int, revision: str) -> str:
    # Reuse validation without reusing the import-proof domain.
    import_proof(key, installation, generation, revision)
    return hmac.new(
        key,
        b"ekonex-wear-drained-v1\0"
        + installation.bytes
        + generation.to_bytes(8, "big")
        + revision.encode(),
        hashlib.sha256,
    ).hexdigest()


async def restore_legacy(
    session: AsyncSession,
    installation: UUID,
    generation: int,
    revision: str,
    proof: str,
    key: bytes,
    *,
    qualified: bool = False,
) -> int:
    """CAS rollback; signed drain receipt represents the producer's outbox state.

    This is an unexposed primitive. Receipt issuance and full writer fencing
    must be qualified before a caller may set `qualified`.
    """
    if qualified is not True:
        raise CutoverBlocked("cutover_not_qualified")
    expected = drain_proof(key, installation, generation, revision)
    if not isinstance(proof, str) or not hmac.compare_digest(expected, proof):
        raise CutoverBlocked("drain_unverified")
    authority = await session.scalar(
        select(WearBridgeAuthority)
        .where(WearBridgeAuthority.installation_id == installation)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if authority is None:
        raise CutoverBlocked("missing_authority")
    if (
        authority.owner == "legacy"
        and authority.generation == generation + 1
        and authority.import_revision == revision
    ):
        return authority.generation
    if (
        authority.owner != "econtrol"
        or authority.generation != generation
        or authority.import_revision != revision
    ):
        raise CutoverBlocked("stale_authority")
    pending = await session.scalar(
        select(WearBridgeWake.device_id)
        .join(WearDevice, WearDevice.id == WearBridgeWake.device_id)
        .where(WearDevice.installation_id == installation)
        .limit(1)
    )
    if pending is not None:
        raise CutoverBlocked("pending_wake")
    authority.owner = "legacy"
    authority.generation = generation + 1
    await session.flush()
    return authority.generation


async def committed_cutover_receipt(
    session: AsyncSession, installation: UUID, generation: int, revision: str, key: bytes
) -> str:
    """Read committed authority in a new caller-owned session, then sign a scoped receipt.

    This helper is not an endpoint. The caller must supply a fresh session created
    after the authority transaction commits; transport and key provisioning remain
    unimplemented and cutover remains disabled.
    """
    import_proof(key, installation, generation, revision)
    if session.in_transaction():
        raise CutoverBlocked("fresh_session_required")
    authority = await session.scalar(
        select(WearBridgeAuthority)
        .where(WearBridgeAuthority.installation_id == installation)
        .execution_options(populate_existing=True)
    )
    if authority is None or (authority.owner, authority.generation, authority.import_revision) != (
        "econtrol",
        generation,
        revision,
    ):
        raise CutoverBlocked("authority_not_committed")
    return hmac.new(
        key,
        b"ekonex-wear-cutover-v1\0"
        + installation.bytes
        + generation.to_bytes(8, "big")
        + revision.encode(),
        hashlib.sha256,
    ).hexdigest()
