"""CHANGE081 official Wear identity and isolation tests."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from apps.cloud_api.app.domain.models import UserInstallationAssignment
from apps.cloud_api.app.wear_devices import (
    WearClaimLimiter,
    WearDenied,
    WearDeviceService,
)
from apps.cloud_api.app.wear_notifications import WearNotificationService


async def grant(session, seeded_domain):
    session.add(
        UserInstallationAssignment(
            tenant_id=seeded_domain.tenant_a_id,
            user_id=seeded_domain.user_a_id,
            installation_id=seeded_domain.installation_a_id,
            granted_by_user_id=seeded_domain.user_a_id,
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_enrollment_claim_is_single_use_and_secret_is_not_stored(session, seeded_domain):
    await grant(session, seeded_domain)
    service = WearDeviceService(session)
    enrollment, code = await service.create_enrollment(
        tenant_id=seeded_domain.tenant_a_id,
        user_id=seeded_domain.user_a_id,
        installation_id=seeded_domain.installation_a_id,
        created_by_user_id=seeded_domain.user_a_id,
        capabilities={"state.read", "voice.ask"},
    )
    assert len(code) == 6 and code.isdecimal()
    device, credential = await service.claim(code, "Xiaomi Watch 2")
    await session.commit()
    assert enrollment.claimed_at is not None
    assert device.credential_hash != credential
    assert code not in enrollment.code_hash
    with pytest.raises(WearDenied):
        await service.claim(code, "Replay")


@pytest.mark.asyncio
async def test_device_context_is_installation_bound_and_revocable(session, seeded_domain):
    await grant(session, seeded_domain)
    service = WearDeviceService(session)
    _offer, code = await service.create_enrollment(
        tenant_id=seeded_domain.tenant_a_id,
        user_id=seeded_domain.user_a_id,
        installation_id=seeded_domain.installation_a_id,
        created_by_user_id=seeded_domain.user_a_id,
        capabilities={"voice.ask"},
    )
    device, credential = await service.claim(code, "Wear")
    await session.commit()
    context = await service.context(credential)
    assert context.installation_id == seeded_domain.installation_a_id
    assert context.capabilities == {"voice.ask"}
    await service.revoke(device_id=device.id, tenant_id=seeded_domain.tenant_a_id)
    await session.commit()
    with pytest.raises(WearDenied):
        await service.context(credential)


@pytest.mark.asyncio
async def test_expired_offer_and_removed_assignment_fail_closed(session, seeded_domain):
    await grant(session, seeded_domain)
    service = WearDeviceService(session)
    offer, code = await service.create_enrollment(
        tenant_id=seeded_domain.tenant_a_id,
        user_id=seeded_domain.user_a_id,
        installation_id=seeded_domain.installation_a_id,
        created_by_user_id=seeded_domain.user_a_id,
        capabilities={"state.read"},
    )
    offer.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    with pytest.raises(WearDenied):
        await service.claim(code, "Expired")

    offer2, code2 = await service.create_enrollment(
        tenant_id=seeded_domain.tenant_a_id,
        user_id=seeded_domain.user_a_id,
        installation_id=seeded_domain.installation_a_id,
        created_by_user_id=seeded_domain.user_a_id,
        capabilities={"state.read"},
    )
    device, credential = await service.claim(code2, "Revoked grant")
    assignment = await session.scalar(
        select(UserInstallationAssignment).where(
            UserInstallationAssignment.user_id == seeded_domain.user_a_id
        )
    )
    assignment.revoked_at = datetime.now(UTC)
    await session.commit()
    assert offer2.claimed_at is not None and device.id is not None
    with pytest.raises(WearDenied):
        await service.context(credential)


@pytest.mark.asyncio
async def test_cross_tenant_enrollment_is_denied(session, seeded_domain):
    await grant(session, seeded_domain)
    with pytest.raises(WearDenied):
        await WearDeviceService(session).create_enrollment(
            tenant_id=seeded_domain.tenant_a_id,
            user_id=seeded_domain.user_a_id,
            installation_id=seeded_domain.installation_b_id,
            created_by_user_id=seeded_domain.user_a_id,
            capabilities={"voice.ask"},
        )


@pytest.mark.asyncio
async def test_device_name_is_bounded_before_rendering(session, seeded_domain):
    await grant(session, seeded_domain)
    service = WearDeviceService(session)
    _offer, code = await service.create_enrollment(
        tenant_id=seeded_domain.tenant_a_id,
        user_id=seeded_domain.user_a_id,
        installation_id=seeded_domain.installation_a_id,
        created_by_user_id=seeded_domain.user_a_id,
        capabilities={"state.read"},
    )
    device, _credential = await service.claim(code, "<script>alert(1)</script>")
    await session.commit()
    assert device.display_name == "<script>alert(1)</script>"


@pytest.mark.asyncio
async def test_claim_rate_limiter_blocks_after_five_failures(session, seeded_domain):
    await grant(session, seeded_domain)
    limiter = WearClaimLimiter(session, "a" * 64)
    assert await limiter.failed_count() == 0
    for expected in range(1, 6):
        limiter.record(successful=False)
        await session.commit()
        assert await limiter.failed_count() == expected
    other = WearClaimLimiter(session, "b" * 64)
    other.record(successful=True)
    await session.commit()
    assert await other.failed_count() == 0


@pytest.mark.asyncio
async def test_notifications_are_deduplicated_device_bound_and_acknowledged(session, seeded_domain):
    await grant(session, seeded_domain)
    devices = WearDeviceService(session)
    _offer, code = await devices.create_enrollment(
        tenant_id=seeded_domain.tenant_a_id,
        user_id=seeded_domain.user_a_id,
        installation_id=seeded_domain.installation_a_id,
        created_by_user_id=seeded_domain.user_a_id,
        capabilities={"notification.receive", "notification.ack"},
    )
    device, credential = await devices.claim(code, "Wear notifiche")
    await session.commit()
    notifications = WearNotificationService(session)
    first = await notifications.enqueue_for_installation(
        tenant_id=seeded_domain.tenant_a_id,
        installation_id=seeded_domain.installation_a_id,
        event_key="event-001",
        category="security",
        severity="warning",
        title="Porta aperta",
        body="La porta ingresso è aperta.",
    )
    duplicate = await notifications.enqueue_for_installation(
        tenant_id=seeded_domain.tenant_a_id,
        installation_id=seeded_domain.installation_a_id,
        event_key="event-001",
        category="security",
        severity="warning",
        title="Porta aperta",
        body="La porta ingresso è aperta.",
    )
    context = await devices.context(credential)
    pending = await notifications.pending(context)
    assert first == 1 and duplicate == 0
    assert [item.device_id for item in pending] == [device.id]
    assert pending[0].delivered_at is not None
    await notifications.acknowledge(context, pending[0].id)
    await session.commit()
    assert await notifications.pending(context) == []


@pytest.mark.asyncio
async def test_notification_capabilities_are_enforced(session, seeded_domain):
    await grant(session, seeded_domain)
    devices = WearDeviceService(session)
    _offer, code = await devices.create_enrollment(
        tenant_id=seeded_domain.tenant_a_id,
        user_id=seeded_domain.user_a_id,
        installation_id=seeded_domain.installation_a_id,
        created_by_user_id=seeded_domain.user_a_id,
        capabilities={"voice.ask"},
    )
    _device, credential = await devices.claim(code, "Wear senza notifiche")
    await session.commit()
    context = await devices.context(credential)
    notifications = WearNotificationService(session)
    assert (
        await notifications.enqueue_for_installation(
            tenant_id=seeded_domain.tenant_a_id,
            installation_id=seeded_domain.installation_a_id,
            event_key="event-no-capability",
            category="system",
            severity="info",
            title="Test",
            body="Non deve essere consegnata.",
        )
        == 0
    )
    with pytest.raises(WearDenied):
        await notifications.pending(context)


@pytest.mark.asyncio
async def test_notification_preferences_are_scoped_to_each_user_and_wear(session, seeded_domain):
    session.add_all(
        [
            UserInstallationAssignment(
                tenant_id=seeded_domain.tenant_a_id,
                user_id=seeded_domain.user_a_id,
                installation_id=seeded_domain.installation_a_id,
                granted_by_user_id=seeded_domain.user_a_id,
            ),
            UserInstallationAssignment(
                tenant_id=seeded_domain.tenant_a_id,
                user_id=seeded_domain.user_readonly_id,
                installation_id=seeded_domain.installation_a_id,
                granted_by_user_id=seeded_domain.user_a_id,
            ),
        ]
    )
    await session.commit()
    devices = WearDeviceService(session)
    _offer_a, code_a = await devices.create_enrollment(
        tenant_id=seeded_domain.tenant_a_id,
        user_id=seeded_domain.user_a_id,
        installation_id=seeded_domain.installation_a_id,
        created_by_user_id=seeded_domain.user_a_id,
        capabilities={"notification.receive", "notification.ack"},
    )
    device_a, credential_a = await devices.claim(code_a, "Wear utente A")
    _offer_b, code_b = await devices.create_enrollment(
        tenant_id=seeded_domain.tenant_a_id,
        user_id=seeded_domain.user_readonly_id,
        installation_id=seeded_domain.installation_a_id,
        created_by_user_id=seeded_domain.user_a_id,
        capabilities={"notification.receive", "notification.ack"},
    )
    device_b, credential_b = await devices.claim(code_b, "Wear utente B")
    device_a.notification_preferences_json = {
        "enabled": True,
        "categories": ["security"],
        "minimum_severity": "warning",
        "vibration": True,
        "quiet_start": None,
        "quiet_end": None,
        "timezone": "Europe/Rome",
    }
    device_b.notification_preferences_json = {
        "enabled": False,
        "categories": ["security"],
        "minimum_severity": "info",
        "vibration": False,
        "quiet_start": None,
        "quiet_end": None,
        "timezone": "Europe/Rome",
    }
    await session.commit()

    notifications = WearNotificationService(session)
    assert (
        await notifications.enqueue_for_installation(
            tenant_id=seeded_domain.tenant_a_id,
            installation_id=seeded_domain.installation_a_id,
            event_key="user-wear-scope",
            category="security",
            severity="warning",
            title="Allarme",
            body="Evento di prova.",
        )
        == 1
    )
    pending_a = await notifications.pending(await devices.context(credential_a))
    pending_b = await notifications.pending(await devices.context(credential_b))
    assert [item.device_id for item in pending_a] == [device_a.id]
    assert pending_b == []
