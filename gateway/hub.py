"""Live fan-out of finished audit rows (feeds the dashboard WebSocket)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Any


def jsonable(row: dict[str, Any]) -> dict[str, Any]:
    """UUIDs, datetimes and decimals as JSON-friendly values."""
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        elif isinstance(value, Decimal):
            out[key] = float(value)
        elif value is not None and not isinstance(value, str | int | float | bool | dict | list):
            out[key] = str(value)
        else:
            out[key] = value
    return out


AUDIT_FIELDS = [
    "id", "ts", "agent_id", "server", "tool", "status", "reason", "injection_score", "pii_entities",
    "tokens_before", "tokens_after", "cost_saved_usd", "latency_total_ms", "latency_downstream_ms",
    "cache_hit",
]  # fmt: skip


def audit_view(row: dict[str, Any]) -> dict[str, Any]:
    """The dashboard's audit-row shape."""
    return jsonable({k: row.get(k) for k in AUDIT_FIELDS})


class AuditHub:
    """In-process fan-out of finished audit rows to WebSocket subscribers."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """New subscriber queue (bounded: slow clients drop old rows)."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove a subscriber."""
        self._subscribers.discard(queue)

    def publish(self, row: dict[str, Any]) -> None:
        """Send one audit row to every subscriber."""
        view = audit_view(row)
        for queue in list(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(view)
