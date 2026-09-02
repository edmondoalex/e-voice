"""Memoria Redis delle interpretazioni conversazionali già validate."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import UUID

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LearnedPhrase:
    key: str
    installation_id: str
    utterance: str
    canonical: str
    intent: str
    hits: int
    approved: bool
    first_seen_at: str
    last_used_at: str


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

    @staticmethod
    def _index_key(tenant_id: UUID) -> str:
        return f"ekonex:conversation-learning-index:{tenant_id}"

    async def get(
        self, tenant_id: UUID, installation_id: UUID, utterance: str
    ) -> str | None:
        try:
            key = self._key(tenant_id, installation_id, utterance)
            value = await self._redis.get(key)
        except Exception as error:  # Redis non deve mai impedire una risposta vocale.
            logger.warning("Conversation learning lookup failed: %s", type(error).__name__)
            return None
        if not isinstance(value, str):
            return None
        try:
            record = LearnedPhrase(**json.loads(value))
            record.hits += 1
            record.last_used_at = datetime.now(UTC).isoformat()
            await self._redis.set(key, json.dumps(asdict(record)), ex=self._ttl_seconds)
            return record.canonical[:500]
        except (TypeError, ValueError, json.JSONDecodeError):
            return value[:500]

    async def remember(
        self,
        tenant_id: UUID,
        installation_id: UUID,
        utterance: str,
        canonical: str,
        intent: str,
    ) -> None:
        try:
            key = self._key(tenant_id, installation_id, utterance)
            now = datetime.now(UTC).isoformat()
            record = LearnedPhrase(
                key=key,
                installation_id=str(installation_id),
                utterance=utterance[:500],
                canonical=canonical[:500],
                intent=intent[:100],
                hits=1,
                approved=False,
                first_seen_at=now,
                last_used_at=now,
            )
            async with self._redis.pipeline(transaction=True) as pipeline:
                pipeline.set(key, json.dumps(asdict(record)), ex=self._ttl_seconds)
                pipeline.zadd(self._index_key(tenant_id), {key: datetime.now(UTC).timestamp()})
                await pipeline.execute()
        except Exception as error:  # Redis non deve mai impedire una risposta vocale.
            logger.warning("Conversation learning save failed: %s", type(error).__name__)

    async def list_for_tenant(self, tenant_id: UUID) -> tuple[LearnedPhrase, ...]:
        try:
            keys = await self._redis.zrevrange(self._index_key(tenant_id), 0, 499)
            values = await self._redis.mget(keys) if keys else []
        except Exception as error:
            logger.warning("Conversation learning list failed: %s", type(error).__name__)
            return ()
        records: list[LearnedPhrase] = []
        for value in values:
            try:
                records.append(LearnedPhrase(**json.loads(value)))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return tuple(records)

    async def approve(self, tenant_id: UUID, key: str) -> bool:
        if not key.startswith(f"ekonex:conversation-learning:{tenant_id}:"):
            return False
        value = await self._redis.get(key)
        if not isinstance(value, str):
            return False
        try:
            record = LearnedPhrase(**json.loads(value))
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        record.approved = True
        await self._redis.set(key, json.dumps(asdict(record)), ex=self._ttl_seconds)
        return True
