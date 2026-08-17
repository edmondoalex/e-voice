"""Async SQLAlchemy infrastructure for future domain persistence."""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, mapped_column

from .config import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

timestamp = Annotated[datetime, mapped_column(nullable=False)]


class Base(DeclarativeBase):
    """Declarative base with deterministic constraint names for migrations."""

    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


async def get_database_session() -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped database session."""

    async with async_session_factory() as session:
        yield session
