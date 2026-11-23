"""Staging-only tenant/installation verifier for a proposed pairing challenge.

No HTTP route, token, administrative grant, approval store or trust bootstrap.
Caller identity must come from the existing authenticated portal session.
"""

import hashlib
import json
import re
import time
from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import AccessDeniedError, AuthenticationService
from .domain.enums import InstallationStatus
from .domain.models import Installation


class ChallengeDenied(ValueError):
    pass


def challenge_digest(challenge: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            challenge, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


async def verify_tenant_approval(
    session: AsyncSession,
    *,
    authenticated_user: UUID,
    tenant: UUID,
    challenge: dict[str, object],
    allowed_roles: frozenset[str],
    now: int | None = None,
) -> dict[str, str]:
    """Check fresh tenant membership and installation; policy defaults to deny."""
    current = int(time.time()) if now is None else now
    try:
        if (
            not isinstance(authenticated_user, UUID)
            or not isinstance(tenant, UUID)
            or not isinstance(allowed_roles, frozenset)
            or not allowed_roles
        ):
            raise ValueError()
        expected = {
            "challenge_id",
            "nonce",
            "issued",
            "expires",
            "tenant",
            "installer_installation",
            "hub_ref",
            "evoice_installation",
            "scope",
        }
        if not isinstance(challenge, dict) or set(challenge) != expected:
            raise ValueError()
        if challenge["scope"] != "wear.bridge.provision" or challenge["tenant"] != str(tenant):
            raise ValueError()
        challenge_id = challenge["challenge_id"]
        evoice_installation = challenge["evoice_installation"]
        installer_installation = challenge["installer_installation"]
        if (
            not isinstance(challenge_id, str)
            or not isinstance(evoice_installation, str)
            or not isinstance(installer_installation, str)
        ):
            raise ValueError()
        UUID(challenge_id)
        installation = UUID(evoice_installation)
        UUID(installer_installation)
        issued = challenge["issued"]
        expires = challenge["expires"]
        if (
            not isinstance(challenge["nonce"], str)
            or not re.fullmatch("[A-Za-z0-9_-]{24,128}", challenge["nonce"])
            or not isinstance(challenge["hub_ref"], str)
            or not re.fullmatch("[A-Za-z0-9_-]{1,128}", challenge["hub_ref"])
            or type(issued) is not int
            or type(expires) is not int
            or issued > current
            or expires <= current
            or not 0 < expires - issued <= 300
        ):
            raise ValueError()
        context = await AuthenticationService(session).tenant_context(
            user_id=authenticated_user, tenant_id=tenant
        )
        if context.role not in allowed_roles:
            raise ValueError()
        instance = await session.scalar(
            select(Installation).where(
                Installation.id == installation,
                Installation.tenant_id == tenant,
                Installation.status == InstallationStatus.ACTIVE,
            )
        )
        if instance is None:
            raise ValueError()
        return {
            "tenant": str(tenant),
            "installation": str(installation),
            "user": str(authenticated_user),
            "challenge_digest": challenge_digest(challenge),
        }
    except (ValueError, TypeError, KeyError, AttributeError, AccessDeniedError):
        raise ChallengeDenied("approval_denied") from None
