"""Interactive one-time bootstrap for the first portal administrator."""

from __future__ import annotations

import argparse
import asyncio
import getpass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import async_session_factory
from .domain.enums import TenantRole
from .domain.models import Dealer, Tenant, TenantMembership, User
from .portal_auth import hash_password


async def bootstrap_first_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    dealer_name: str,
    dealer_slug: str,
    tenant_name: str,
    tenant_slug: str,
    role: TenantRole,
) -> User:
    """Create the first user and its verified tenant membership, once only."""
    if int(await session.scalar(select(func.count(User.id))) or 0) != 0:
        raise RuntimeError("bootstrap refused: at least one user already exists")
    dealer = Dealer(name=dealer_name.strip(), slug=dealer_slug.strip().lower())
    tenant = Tenant(name=tenant_name.strip(), slug=tenant_slug.strip().lower(), dealer=dealer)
    user = User(email=email.strip().lower(), password_hash=hash_password(password))
    session.add_all([dealer, tenant, user])
    await session.flush()
    session.add(TenantMembership(tenant_id=tenant.id, user_id=user.id, role=role))
    await session.commit()
    return user


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--dealer-name", required=True)
    parser.add_argument("--dealer-slug", required=True)
    parser.add_argument("--tenant-name", required=True)
    parser.add_argument("--tenant-slug", required=True)
    parser.add_argument(
        "--role",
        choices=[role.value for role in TenantRole if role is not TenantRole.SUPPORT_READONLY],
        default=TenantRole.OWNER.value,
    )
    return parser.parse_args()


async def _run() -> None:
    arguments = _arguments()
    password = getpass.getpass("Password (minimum 12 characters): ")
    confirmation = getpass.getpass("Repeat password: ")
    if password != confirmation:
        raise SystemExit("passwords do not match")
    async with async_session_factory() as session:
        user = await bootstrap_first_user(
            session,
            email=arguments.email,
            password=password,
            dealer_name=arguments.dealer_name,
            dealer_slug=arguments.dealer_slug,
            tenant_name=arguments.tenant_name,
            tenant_slug=arguments.tenant_slug,
            role=TenantRole(arguments.role),
        )
    print(f"Portal user created: {user.email} ({user.id})")


if __name__ == "__main__":
    asyncio.run(_run())
