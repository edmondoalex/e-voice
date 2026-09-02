"""Memoria Redis delle interpretazioni conversazionali già validate."""

from __future__ import annotations

import hashlib
import logging
import re
from uuid import UUID

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class ConversationLearningStore:
    """Memorizza solo query canoniche che il motore deterministico ha già risolto."""

    def __init__(self, redis_url: str, ttl_seconds: int) -> None:
        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def _key(tenant_id: UUID, installation_id: UUID, utterance: str) -> str:
        normalized = re.sub(r"\s+", " ", utterance.casefold()).strip()
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return f"ekonex:conversation-learning:{tenant_id}:{installation_id}:{digest}"

    async def get(
        self, tenant_id: UUID, installation_id: UUID, utterance: str
    ) -> str | None:
        try:
            value = await self._redis.get(self._key(tenant_id, installation_id, utterance))
        except Exception as error:  # Redis non deve mai impedire una risposta vocale.
            logger.warning("Conversation learning lookup failed: %s", type(error).__name__)
            return None
        return value[:500] if isinstance(value, str) else None

    async def remember(
        self,
        tenant_id: UUID,
        installation_id: UUID,
        utterance: str,
        canonical: str,
    ) -> None:
        try:
            await self._redis.set(
                self._key(tenant_id, installation_id, utterance),
                canonical[:500],
                ex=self._ttl_seconds,
            )
        except Exception as error:  # Redis non deve mai impedire una risposta vocale.
            logger.warning("Conversation learning save failed: %s", type(error).__name__)
