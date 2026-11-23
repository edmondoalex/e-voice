"""Staging-only tenant/installation verifier for a proposed pairing challenge.

No HTTP route, token, administrative grant, approval store or trust bootstrap.
Caller identity must come from the existing authenticated portal session.
"""

import hashlib
import json
import re
import time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import AccessDeniedError, AuthenticationService
from .domain.enums import InstallationStatus
from .domain.models import Installation


class ChallengeDenied(ValueError):
    pass


def challenge_digest(challenge):
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
    challenge: dict,
    allowed_roles: frozenset,
    now=None,
):
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
        UUID(challenge["challenge_id"])
        installation = UUID(challenge["evoice_installation"])
        UUID(challenge["installer_installation"])
        if (
            not isinstance(challenge["nonce"], str)
            or not re.fullmatch("[A-Za-z0-9_-]{24,128}", challenge["nonce"])
            or not isinstance(challenge["hub_ref"], str)
            or not re.fullmatch("[A-Za-z0-9_-]{1,128}", challenge["hub_ref"])
            or type(challenge["issued"]) is not int
            or type(challenge["expires"]) is not int
            or challenge["issued"] > current
            or challenge["expires"] <= current
            or not 0 < challenge["expires"] - challenge["issued"] <= 300
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
