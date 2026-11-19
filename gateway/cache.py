"""Downstream result cache (D3): raw results of read-only tools, keyed by server, tool and arguments.

A cache hit still runs the whole response pipeline (PII mask, injection scan, pruning), because the
cache sits at the downstream boundary. Tools without ``readOnlyHint=true`` (e.g. ``transfer_funds``)
are never cached. Redis when ``REDIS_URL`` is set, otherwise an in-memory TTL map.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Protocol

from mcp.types import CallToolResult, Tool

KEY_PREFIX = "cache:"


def cache_key(server: str, tool: str, arguments: dict[str, Any]) -> str:
    """Stable key for one call."""
    payload = json.dumps([server, tool, arguments], sort_keys=True, default=str)
    return KEY_PREFIX + hashlib.sha256(payload.encode()).hexdigest()


def is_cacheable(tool: Tool | None) -> bool:
    """Only tools that declare themselves read-only."""
    return bool(tool is not None and tool.annotations is not None and tool.annotations.read_only_hint)


class ResultCache(Protocol):
    """Cache backend."""

    async def get(self, key: str) -> CallToolResult | None:
        """Cached result or None."""
        ...

    async def set(self, key: str, result: CallToolResult, ttl_s: int) -> None:
        """Store a result."""
        ...

    async def close(self) -> None:
        """Release connections."""
        ...


def _dump(result: CallToolResult) -> str:
    return result.model_dump_json(by_alias=True, exclude_none=True)


class MemoryResultCache:
    """Process-local TTL cache."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[float, str]] = {}

    async def get(self, key: str) -> CallToolResult | None:
        """Cached result or None."""
        item = self._items.get(key)
        if item is None or item[0] < time.monotonic():
            self._items.pop(key, None)
            return None
        return CallToolResult.model_validate_json(item[1])

    async def set(self, key: str, result: CallToolResult, ttl_s: int) -> None:
        """Store a result."""
        self._items[key] = (time.monotonic() + ttl_s, _dump(result))

    async def close(self) -> None:
        """Nothing to release."""


class RedisResultCache:
    """Redis-backed cache."""

    def __init__(self, url: str) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(url, decode_responses=True)  # type: ignore[no-untyped-call]

    async def get(self, key: str) -> CallToolResult | None:
        """Cached result or None."""
        raw = await self._redis.get(key)
        return CallToolResult.model_validate_json(raw) if raw else None

    async def set(self, key: str, result: CallToolResult, ttl_s: int) -> None:
        """Store a result."""
        await self._redis.set(key, _dump(result), ex=ttl_s)

    async def close(self) -> None:
        """Close the pool."""
        await self._redis.aclose()
