"""Evidence recovery store (D8): full results whose fields were pruned, kept for a short TTL.

The optimizer saves the unpruned (already PII-masked) result under ``omitted:<ref>``; the agent can
call the virtual tool ``aegis__fetch_omitted`` with that ref to get the removed fields back.
Redis is used when ``REDIS_URL`` is set; otherwise an in-memory store (single process, tests).
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Protocol

KEY_PREFIX = "omitted:"


class EvidenceStore(Protocol):
    """Where omitted results are kept."""

    async def put(self, data: dict[str, Any], ttl_s: int) -> str:
        """Save a full result; return its ref."""
        ...

    async def get(self, ref: str) -> dict[str, Any] | None:
        """The full result, or None if unknown or expired."""
        ...

    async def close(self) -> None:
        """Release connections."""
        ...


class MemoryEvidenceStore:
    """Process-local store with expiry (fallback when Redis is not configured)."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[float, dict[str, Any]]] = {}

    async def put(self, data: dict[str, Any], ttl_s: int) -> str:
        """Save and return a new ref."""
        ref = uuid.uuid4().hex[:16]
        self._items[ref] = (time.monotonic() + ttl_s, data)
        return ref

    async def get(self, ref: str) -> dict[str, Any] | None:
        """Read a ref (expired refs are deleted)."""
        item = self._items.get(ref)
        if item is None:
            return None
        if item[0] < time.monotonic():
            del self._items[ref]
            return None
        return item[1]

    async def close(self) -> None:
        """Nothing to release."""


class RedisEvidenceStore:
    """Redis-backed store (``SET omitted:<ref> <json> EX ttl``)."""

    def __init__(self, url: str) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(url, decode_responses=True)  # type: ignore[no-untyped-call]

    async def put(self, data: dict[str, Any], ttl_s: int) -> str:
        """Save and return a new ref."""
        ref = uuid.uuid4().hex[:16]
        await self._redis.set(KEY_PREFIX + ref, json.dumps(data, default=str), ex=ttl_s)
        return ref

    async def get(self, ref: str) -> dict[str, Any] | None:
        """Read a ref."""
        raw = await self._redis.get(KEY_PREFIX + ref)
        return json.loads(raw) if raw else None

    async def close(self) -> None:
        """Close the connection pool."""
        await self._redis.aclose()


def pick(data: dict[str, Any], fields: list[str] | None) -> dict[str, Any]:
    """Subset of a stored result: top-level keys, or dotted paths (``holdings[].instrument``)."""
    if not fields:
        return data
    out: dict[str, Any] = {}
    for path in fields:
        top = path.split(".")[0].removesuffix("[]")
        if top in data:
            out[top] = data[top]
    return out
