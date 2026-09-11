"""Create a one-time e-Face Bearer credential for one installation."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import secrets
from uuid import UUID

from sqlalchemy import or_, select

from .database import async_session_factory
from .domain.models import Installation, MediaApiCredential


async def create_media_credential(
    installation_reference: str, name: str
) -> tuple[str, Installation]:
    """Store only the digest and return the secret exactly once."""
    async with async_session_factory() as session:
        try:
            installation_uuid = UUID(installation_reference)
        except ValueError:
            installation_uuid = UUID(int=0)
        installation = await session.scalar(
            select(Installation).where(
                or_(
                    Installation.public_id == installation_reference,
                    Installation.id == installation_uuid,
                )
            )
        )
        if installation is None:
            raise RuntimeError("installation not found")
        token = "emf_" + secrets.token_urlsafe(48)
        session.add(
            MediaApiCredential(
                tenant_id=installation.tenant_id,
                installation_id=installation.id,
                name=name.strip()[:100],
                secret_hash=hashlib.sha256(token.encode()).hexdigest(),
            )
        )
        await session.commit()
        return token, installation


async def _run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installation", required=True, help="Installation UUID or public_id")
    parser.add_argument("--name", default="e-Face X4")
    arguments = parser.parse_args()
    token, installation = await create_media_credential(arguments.installation, arguments.name)
    print(f"installation_id={installation.id}")
    print(f"bearer_token={token}")
    print("Store this token now: it cannot be displayed again.")


if __name__ == "__main__":
    asyncio.run(_run())
