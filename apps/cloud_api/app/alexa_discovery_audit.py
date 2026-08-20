"""Tenant-safe, secret-free observation of the latest Alexa Discovery response."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .domain.models import (
    AlexaDiscoveryDelivery,
    AlexaDiscoverySnapshot,
    Entity,
    Installation,
)


def _changes(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    before = {str(item["endpoint_id"]): item for item in previous}
    after = {str(item["endpoint_id"]): item for item in current}
    changes: list[dict[str, Any]] = []
    for endpoint_id, item in after.items():
        old = before.get(endpoint_id)
        if old is None:
            changes.append({**item, "change": "new"})
        elif old.get("voice_name") != item.get("voice_name"):
            changes.append(
                {
                    **item,
                    "change": "renamed",
                    "previous_voice_name": old.get("voice_name"),
                }
            )
    for endpoint_id, item in before.items():
        if endpoint_id not in after:
            changes.append({**item, "change": "removed"})
    return changes


async def record_discovery(
    database: AsyncSession,
    tenant_id: UUID,
    link_id: UUID,
    installations: Sequence[Installation],
    published: Sequence[tuple[Entity, dict[str, Any]]],
) -> None:
    """Replace each installation's latest observation using the actual mapped endpoints."""
    existing = {
        item.installation_id: item
        for item in (
            await database.scalars(
                select(AlexaDiscoverySnapshot).where(AlexaDiscoverySnapshot.tenant_id == tenant_id)
            )
        ).all()
    }
    by_installation: dict[UUID, list[dict[str, Any]]] = {
        installation.id: [] for installation in installations
    }
    for entity, endpoint in published:
        by_installation.setdefault(entity.installation_id, []).append(
            {
                "endpoint_id": str(endpoint["endpointId"]),
                "voice_name": str(endpoint["friendlyName"]),
                "domain": entity.ha_domain,
            }
        )
    now = datetime.now(UTC)
    deliveries = list(
        (
            await database.scalars(
                select(AlexaDiscoveryDelivery).where(AlexaDiscoveryDelivery.link_id == link_id)
            )
        ).all()
    )
    delivery_by_endpoint = {item.alexa_endpoint_id: item for item in deliveries}
    published_ids: set[str] = set()
    for entity, endpoint in published:
        endpoint_value = str(endpoint["endpointId"])
        published_ids.add(endpoint_value)
        fingerprint = hashlib.sha256(
            json.dumps(endpoint, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        delivery = delivery_by_endpoint.get(endpoint_value)
        if delivery is None:
            delivery = AlexaDiscoveryDelivery(
                link_id=link_id,
                installation_id=entity.installation_id,
                entity_id=entity.id,
                alexa_endpoint_id=endpoint_value,
                representation_fingerprint=fingerprint,
                published_at=now,
            )
            database.add(delivery)
        delivery.entity_id = entity.id
        delivery.representation_fingerprint = fingerprint
        delivery.published_at = now
        delivery.removed_at = None
    installation_ids = {installation.id for installation in installations}
    for delivery in deliveries:
        if (
            delivery.installation_id in installation_ids
            and delivery.alexa_endpoint_id not in published_ids
            and delivery.removed_at is None
        ):
            delivery.removed_at = now
    for installation in installations:
        current = sorted(
            by_installation[installation.id], key=lambda item: str(item["endpoint_id"])
        )
        snapshot = existing.get(installation.id)
        previous = snapshot.endpoints_json if snapshot is not None else []
        changes = _changes(previous, current)
        if snapshot is None:
            snapshot = AlexaDiscoverySnapshot(
                tenant_id=tenant_id,
                installation_id=installation.id,
            )
            database.add(snapshot)
        snapshot.discovered_at = now
        snapshot.endpoint_count = len(current)
        snapshot.endpoints_json = current
        snapshot.changes_json = changes
    await database.commit()
