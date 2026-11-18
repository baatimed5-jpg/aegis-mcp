"""Audit middleware: every call gets one ``audit_call`` row, whatever its outcome (D2).

``on_request`` writes the row as ``in_progress`` before the downstream call (so a crash still
leaves a trace); ``on_complete`` upserts the final state. A call denied before the audit step
has no ``in_progress`` row, so the final upsert inserts it.
"""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from gateway.db.models import AuditCall
from gateway.db.session import make_engine, make_sessionmaker
from gateway.pipeline.base import Middleware
from gateway.pipeline.context import CallContext


def audit_row(ctx: CallContext) -> dict[str, Any]:
    """Map a call context to ``audit_call`` column values."""
    return {
        "id": ctx.call_id,
        "run_id": ctx.run_id,
        "ts": ctx.ts,
        "agent_id": ctx.agent_id,
        "server": ctx.server,
        "tool": ctx.tool,
        "arguments": ctx.arguments,
        "status": ctx.status,
        "reason": ctx.reason,
        "injection_score": ctx.extra.get("injection_score"),
        "pii_entities": ctx.extra.get("pii_entities"),
        "tokens_before": ctx.extra.get("tokens_before"),
        "tokens_after": ctx.extra.get("tokens_after"),
        "cost_saved_usd": ctx.extra.get("cost_saved_usd"),
        "latency_total_ms": ctx.latency_total_ms,
        "latency_downstream_ms": ctx.latency_downstream_ms,
        "cache_hit": ctx.cache_hit,
    }


class AuditStore(Protocol):
    """Where audit rows go."""

    async def upsert(self, row: dict[str, Any]) -> None:
        """Insert the row, or update it if its id already exists."""

    async def close(self) -> None:
        """Release resources."""


class PostgresAuditStore:
    """Audit store backed by the ``audit_call`` table."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.sessions: async_sessionmaker[AsyncSession] = make_sessionmaker(engine)

    @classmethod
    def from_url(cls, database_url: str) -> PostgresAuditStore:
        """Create a store with its own engine."""
        return cls(make_engine(database_url))

    async def upsert(self, row: dict[str, Any]) -> None:
        """``INSERT ... ON CONFLICT (id) DO UPDATE`` with every column except the key."""
        stmt = insert(AuditCall).values(**row)
        update_cols = {k: stmt.excluded[k] for k in row if k != "id"}
        stmt = stmt.on_conflict_do_update(index_elements=[AuditCall.id], set_=update_cols)
        async with self.sessions.begin() as session:
            await session.execute(stmt)

    async def close(self) -> None:
        """Dispose of the engine's connection pool."""
        await self.engine.dispose()


class AuditMiddleware(Middleware):
    """Writes every call to the audit store."""

    name = "audit"

    def __init__(self, store: AuditStore) -> None:
        self.store = store

    async def on_request(self, ctx: CallContext) -> None:
        """Record the call as in progress (fails closed if the store is unreachable)."""
        await self.store.upsert(audit_row(ctx))

    async def on_complete(self, ctx: CallContext) -> None:
        """Record the final status, reason and latencies."""
        await self.store.upsert(audit_row(ctx))

    async def shutdown(self) -> None:
        """Close the store."""
        await self.store.close()
