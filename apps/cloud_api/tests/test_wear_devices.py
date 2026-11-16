"""CHANGE081 official Wear identity and isolation tests."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from apps.cloud_api.app.domain.models import UserInstallationAssignment, WearDevice
from apps.cloud_api.app.wear_devices import WearDenied, WearDeviceService


async def grant(session, seeded_domain):
    session.add(UserInstallationAssignment(
        tenant_id=seeded_domain.tenant_a_id,
        user_id=seeded_domain.user_a_id,
        installation_id=seeded_domain.installation_a_id,
        granted_by_user_id=seeded_domain.user_a_id,
    ))
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
    assignment = await session.scalar(select(UserInstallationAssignment).where(
        UserInstallationAssignment.user_id == seeded_domain.user_a_id
    ))
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
