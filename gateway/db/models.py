"""SQLAlchemy 2 ORM models of the gateway database (ARCHITECTURE §3.1).

Phase 2 created ``experiment_run`` and ``audit_call``; Phase 3 adds ``approval`` and ``security_event``.
The other tables arrive with their phases.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all gateway tables."""


class ExperimentRun(Base):
    """One evaluation run; groups audit rows (used from Phase 4)."""

    __tablename__ = "experiment_run"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    config_name: Mapped[str] = mapped_column(String(100))
    run_index: Mapped[int] = mapped_column(Integer, default=0)
    git_sha: Mapped[str | None] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditCall(Base):
    """One ``tools/call`` seen by the gateway, whatever its outcome."""

    __tablename__ = "audit_call"
    __table_args__ = (
        Index("ix_audit_call_ts", "ts"),
        Index("ix_audit_call_agent_ts", "agent_id", "ts"),
        Index("ix_audit_call_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiment_run.id", ondelete="SET NULL")
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    agent_id: Mapped[str] = mapped_column(String(100))
    server: Mapped[str] = mapped_column(String(100))
    tool: Mapped[str] = mapped_column(String(200))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(Text)
    injection_score: Mapped[float | None]
    pii_entities: Mapped[int | None]
    tokens_before: Mapped[int | None]
    tokens_after: Mapped[int | None]
    cost_saved_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    latency_total_ms: Mapped[float | None]
    latency_downstream_ms: Mapped[float | None]
    cache_hit: Mapped[bool] = mapped_column(default=False)


class Approval(Base):
    """A human decision on a call whose tool needs approval (D7).

    ``audit_call_id`` has no foreign key: the approval is requested by the policy step, before the
    audit step has written the call's row (D21). The call's agent, tool and arguments are copied so
    a reviewer can decide without a join.
    """

    __tablename__ = "approval"
    __table_args__ = (Index("ix_approval_status_created", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    audit_call_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    agent_id: Mapped[str] = mapped_column(String(100))
    tool: Mapped[str] = mapped_column(String(300))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(100))


class SecurityEvent(Base):
    """A detection: an injection in a tool description, an argument or a response."""

    __tablename__ = "security_event"
    __table_args__ = (Index("ix_security_event_ts", "ts"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    audit_call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audit_call.id", ondelete="CASCADE"), index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(20))
    server: Mapped[str] = mapped_column(String(100))
    tool: Mapped[str] = mapped_column(String(200))
    detector: Mapped[str] = mapped_column(String(50))
    score: Mapped[float]
    action: Mapped[str] = mapped_column(String(20))
    excerpt: Mapped[str | None] = mapped_column(Text)
