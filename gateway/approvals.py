"""Approval queue (D7): a call waits on an in-memory event while a human decides via the REST API.

Each request also gets an ``approval`` row (pending → approved | denied | timeout). Waiters live in
this process only, so approvals work with a single gateway instance (documented limitation).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import anyio

from gateway.pipeline.context import CallContext
from gateway.pipeline.errors import PipelineDenied
from gateway.store import GatewayStore

log = logging.getLogger(__name__)

ApprovalStatus = Literal["pending", "approved", "denied", "timeout"]


class ApprovalNotFound(KeyError):  # noqa: N818 - mirrors HTTP 404 semantics
    """No pending approval with this id (unknown, already decided or timed out)."""


@dataclass
class PendingApproval:
    """An approval that is still waiting for a decision."""

    id: uuid.UUID
    audit_call_id: uuid.UUID
    agent_id: str
    tool: str
    arguments: dict[str, Any]
    created_at: datetime
    event: anyio.Event = field(default_factory=anyio.Event)
    status: ApprovalStatus = "pending"
    decided_by: str | None = None

    def public(self) -> dict[str, Any]:
        """JSON-friendly view for the REST API."""
        return {
            "id": str(self.id),
            "audit_call_id": str(self.audit_call_id),
            "agent_id": self.agent_id,
            "tool": self.tool,
            "arguments": self.arguments,
            "created_at": self.created_at.isoformat(),
            "status": self.status,
        }


class ApprovalService:
    """Creates approvals, waits for decisions and applies them."""

    def __init__(self, store: GatewayStore, timeout_s: float = 120.0) -> None:
        self.store = store
        self.timeout_s = timeout_s
        self._pending: dict[uuid.UUID, PendingApproval] = {}

    def pending(self) -> list[PendingApproval]:
        """Approvals waiting for a decision, oldest first."""
        return sorted(self._pending.values(), key=lambda p: p.created_at)

    async def request(self, ctx: CallContext) -> None:
        """Block until the call is approved; raise ``PipelineDenied`` if denied or timed out."""
        item = PendingApproval(
            id=uuid.uuid4(),
            audit_call_id=ctx.call_id,
            agent_id=ctx.agent_id,
            tool=ctx.public_name,
            arguments=ctx.audit_arguments if ctx.audit_arguments is not None else ctx.arguments,
            created_at=datetime.now(UTC),
        )
        await self.store.insert_approval(
            {
                "id": item.id,
                "audit_call_id": item.audit_call_id,
                "agent_id": item.agent_id,
                "tool": item.tool,
                "arguments": item.arguments,
                "status": "pending",
                "created_at": item.created_at,
            }
        )
        self._pending[item.id] = item
        ctx.extra["approval_id"] = str(item.id)
        log.info("approval %s pending for %s by %s", item.id, item.tool, item.agent_id)
        try:
            with anyio.move_on_after(self.timeout_s):
                await item.event.wait()
        finally:
            self._pending.pop(item.id, None)
        if item.status == "pending":
            item.status = "timeout"
            await self.store.update_approval(item.id, {"status": "timeout", "decided_at": datetime.now(UTC)})
            raise PipelineDenied(
                f"no approval decision within {self.timeout_s:g} s", code="approval_timeout", status="timeout"
            )
        if item.status == "denied":
            raise PipelineDenied(f"denied by {item.decided_by}", code="approval_denied", status="denied")
        ctx.approved = True

    async def decide(self, approval_id: uuid.UUID, approve: bool, decided_by: str) -> PendingApproval:
        """Approve or deny a pending call and wake it up."""
        item = self._pending.get(approval_id)
        if item is None or item.status != "pending":
            raise ApprovalNotFound(str(approval_id))
        item.status = "approved" if approve else "denied"
        item.decided_by = decided_by
        await self.store.update_approval(
            approval_id, {"status": item.status, "decided_at": datetime.now(UTC), "decided_by": decided_by}
        )
        item.event.set()
        return item
