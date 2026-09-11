"""Small installation-scoped realtime broker for the internal Media API."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class MediaEventBroker:
    """Keep a bounded recovery window and fan events out to SSE clients."""

    def __init__(self, *, history_size: int = 512) -> None:
        self._history: dict[UUID, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=history_size)
        )
        self._subscribers: dict[UUID, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._revisions: dict[UUID, int] = defaultdict(int)

    def current_revision(self, installation_id: UUID, baseline: int = 0) -> int:
        """Return the backend application revision, seeded by the EVCP baseline."""
        self._revisions[installation_id] = max(self._revisions[installation_id], baseline)
        return self._revisions[installation_id]

    def publish(
        self,
        installation_id: UUID,
        installation_revision: int,
        event_type: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        revision = max(
            installation_revision,
            self._revisions[installation_id] + 1,
        )
        self._revisions[installation_id] = revision
        event = {
            "event_id": str(uuid4()),
            "installation_id": str(installation_id),
            "installation_revision": revision,
            "type": event_type,
            "occurred_at": _now(),
            "observed_at": _now(),
            "data": data,
        }
        self._history[installation_id].append(event)
        for queue in tuple(self._subscribers[installation_id]):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)
        return event

    def events_after(self, installation_id: UUID, revision: int) -> list[dict[str, Any]] | None:
        history = self._history[installation_id]
        if not history:
            return []
        oldest = int(history[0]["installation_revision"])
        if revision < oldest - 1:
            return None
        return [item for item in history if int(item["installation_revision"]) > revision]

    def subscribe(self, installation_id: UUID) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=128)
        self._subscribers[installation_id].add(queue)
        return queue

    def unsubscribe(self, installation_id: UUID, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers[installation_id].discard(queue)


media_events = MediaEventBroker()
