"""Focused contract tests for the internal Ekonex Media API."""

import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app.command_dispatch import command_adapter
from apps.cloud_api.app.domain.models import MediaApiCredential
from apps.cloud_api.app.media_api import (
    _arguments,
    _groups,
    _installation,
    _media_context,
    _public_status,
    _valid_image,
    media_api_status,
)
from apps.cloud_api.app.media_realtime import MediaEventBroker


async def test_media_api_status_is_public_and_secret_free() -> None:
    payload = await media_api_status()
    assert payload == {
        "service": "ekonex_media",
        "api_version": "v1",
        "status": "active",
        "authentication": ["portal_cookie", "bearer"],
    }


def player(registry_id: str, entity_id: str, members: list[str]) -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        ha_registry_id=registry_id,
        ha_entity_id=entity_id,
        attributes_json={"group_members": members},
        updated_at=now,
        last_seen_at=now,
    )


def test_group_identity_is_order_independent_and_has_no_invented_coordinator() -> None:
    installation_id = uuid4()
    first = player("registry-a", "media_player.a", ["media_player.a", "media_player.b"])
    second = player("registry-b", "media_player.b", ["media_player.b", "media_player.a"])

    forward = _groups([first, second], installation_id)
    reverse = _groups([second, first], installation_id)

    assert forward[0]["group_id"] == reverse[0]["group_id"]
    assert forward[0]["completeness"] == "complete"
    assert forward[0]["coordinator_registry_id"] is None


def test_group_is_inconsistent_when_not_all_members_declare_it() -> None:
    installation_id = uuid4()
    first = player("registry-a", "media_player.a", ["media_player.a", "media_player.b"])
    second = player("registry-b", "media_player.b", [])
    assert _groups([first, second], installation_id)[0]["completeness"] == "inconsistent"


def test_typed_media_arguments_reject_extra_and_duplicate_members() -> None:
    with pytest.raises(ValueError):
        _arguments("set_volume", {"volume_percent": 30, "service": "turn_on"})
    with pytest.raises(ValueError):
        _arguments("media_join", {"member_registry_ids": ["one", "one"]})

    command = command_adapter.validate_python(
        {"operation": "media_join", "member_registry_ids": ["one", "two"]}
    )
    assert command.member_registry_ids == ["one", "two"]


def test_group_partial_failure_is_exposed_as_partial() -> None:
    response = {"members": [{"registry_id": "one", "status": "failed"}]}
    assert _public_status("execution_failed", response) == "partial_failure"


def test_artwork_magic_bytes_are_checked_and_svg_is_rejected() -> None:
    assert _valid_image("image/png", b"\x89PNG\r\n\x1a\ncontent")
    assert not _valid_image("image/png", b"not-a-png")
    assert not _valid_image("image/svg+xml", b"<svg><script/></svg>")


def test_realtime_recovery_is_installation_scoped_and_bounded() -> None:
    broker = MediaEventBroker(history_size=2)
    first, second = uuid4(), uuid4()
    broker.publish(first, 1, "player.updated", {"registry_id": "one"})
    broker.publish(first, 2, "player.updated", {"registry_id": "one"})
    broker.publish(first, 3, "player.updated", {"registry_id": "one"})
    broker.publish(second, 1, "player.updated", {"registry_id": "other"})

    assert broker.events_after(first, 0) is None
    assert [item["installation_revision"] for item in broker.events_after(first, 1) or []] == [2, 3]
    assert [item["installation_revision"] for item in broker.events_after(second, 0) or []] == [1]


def test_player_command_schema_is_strict() -> None:
    with pytest.raises(ValidationError):
        command_adapter.validate_python(
            {"operation": "media_join", "member_registry_ids": ["one"], "service": "join"}
        )


async def test_media_bearer_is_hash_only_and_installation_scoped(
    session: AsyncSession, seeded_domain: object
) -> None:
    token = "emf_test-only-secret"
    credential = MediaApiCredential(
        tenant_id=seeded_domain.tenant_a_id,  # type: ignore[attr-defined]
        installation_id=seeded_domain.installation_a_id,  # type: ignore[attr-defined]
        name="e-Face test",
        secret_hash=hashlib.sha256(token.encode()).hexdigest(),
    )
    session.add(credential)
    await session.commit()

    context = await _media_context(None, session, f"Bearer {token}")
    assert context.installation_id == seeded_domain.installation_a_id  # type: ignore[attr-defined]
    assert context.user_id is None
    assert token not in credential.secret_hash

    with pytest.raises(HTTPException) as denied:
        await _installation(
            session,
            context,
            seeded_domain.installation_b_id,  # type: ignore[attr-defined]
        )
    assert denied.value.status_code == 404
