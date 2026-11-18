"""Gateway persistence: audit rows, security events and approvals behind one interface.

One store (one connection pool) is shared by all middlewares and the REST API. Tests use an
in-memory implementation of the same protocol.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from gateway.db.models import Approval, AuditCall, SecurityEvent
from gateway.db.session import make_engine, make_sessionmaker


class GatewayStore(Protocol):
    """Where the gateway writes its records."""

    async def upsert_audit(self, row: dict[str, Any], events: list[dict[str, Any]] | None = None) -> None:
        """Insert or update an ``audit_call`` row, then add its security events (same transaction)."""

    async def add_security_events(self, events: list[dict[str, Any]]) -> None:
        """Insert security events that belong to no call (e.g. tool-description scans)."""

    async def insert_approval(self, row: dict[str, Any]) -> None:
        """Insert a pending approval."""

    async def update_approval(self, approval_id: uuid.UUID, values: dict[str, Any]) -> None:
        """Record an approval decision (status, decided_at, decided_by)."""

    async def close(self) -> None:
        """Release resources."""


class PostgresStore:
    """``GatewayStore`` backed by PostgreSQL (tables in ``gateway.db.models``)."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.sessions: async_sessionmaker[AsyncSession] = make_sessionmaker(engine)

    @classmethod
    def from_url(cls, database_url: str) -> PostgresStore:
        """Create a store with its own engine."""
        return cls(make_engine(database_url))

    async def upsert_audit(self, row: dict[str, Any], events: list[dict[str, Any]] | None = None) -> None:
        """``INSERT ... ON CONFLICT (id) DO UPDATE`` on ``audit_call``, plus its events."""
        stmt = insert(AuditCall).values(**row)
        update_cols = {k: stmt.excluded[k] for k in row if k != "id"}
        stmt = stmt.on_conflict_do_update(index_elements=[AuditCall.id], set_=update_cols)
        async with self.sessions.begin() as session:
            await session.execute(stmt)
            if events:
                await session.execute(insert(SecurityEvent), events)

    async def add_security_events(self, events: list[dict[str, Any]]) -> None:
        """Insert events without a call."""
        if not events:
            return
        async with self.sessions.begin() as session:
            await session.execute(insert(SecurityEvent), events)

    async def insert_approval(self, row: dict[str, Any]) -> None:
        """Insert a pending approval row."""
        async with self.sessions.begin() as session:
            await session.execute(insert(Approval).values(**row))

    async def update_approval(self, approval_id: uuid.UUID, values: dict[str, Any]) -> None:
        """Update an approval row."""
        async with self.sessions.begin() as session:
            await session.execute(update(Approval).where(Approval.id == approval_id).values(**values))

    async def close(self) -> None:
        """Dispose of the connection pool."""
        await self.engine.dispose()


class MemoryStore:
    """In-memory ``GatewayStore`` for tests and DB-less runs. Keeps the latest version of each row."""

    def __init__(self) -> None:
        self.audit: dict[Any, dict[str, Any]] = {}
        self.audit_writes: list[str] = []
        self.events: list[dict[str, Any]] = []
        self.approvals: dict[uuid.UUID, dict[str, Any]] = {}
        self.closed = False

    async def upsert_audit(self, row: dict[str, Any], events: list[dict[str, Any]] | None = None) -> None:
        """Store the row (and events)."""
        self.audit[row["id"]] = dict(row)
        self.audit_writes.append(row["status"])
        self.events.extend(events or [])

    async def add_security_events(self, events: list[dict[str, Any]]) -> None:
        """Store events."""
        self.events.extend(events)

    async def insert_approval(self, row: dict[str, Any]) -> None:
        """Store an approval."""
        self.approvals[row["id"]] = dict(row)

    async def update_approval(self, approval_id: uuid.UUID, values: dict[str, Any]) -> None:
        """Update an approval."""
        self.approvals[approval_id].update(values)

    async def close(self) -> None:
        """Mark as closed."""
        self.closed = True
