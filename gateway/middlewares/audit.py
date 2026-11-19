"""Audit middleware: every call gets one ``audit_call`` row, whatever its outcome (D2).

``on_request`` writes the row as ``in_progress`` before the downstream call (so a crash still
leaves a trace); ``on_complete`` upserts the final state together with the call's security events.
A call denied before the audit step has no ``in_progress`` row, so the final upsert inserts it.
Arguments are stored masked when the PII middleware ran (``ctx.audit_arguments``).
"""

from __future__ import annotations

from typing import Any

from gateway.hub import AuditHub
from gateway.pipeline.base import Middleware
from gateway.pipeline.context import CallContext
from gateway.store import GatewayStore

SECURITY_EVENTS_KEY = "security_events"


def audit_row(ctx: CallContext) -> dict[str, Any]:
    """Map a call context to ``audit_call`` column values."""
    return {
        "id": ctx.call_id,
        "run_id": ctx.run_id,
        "ts": ctx.ts,
        "agent_id": ctx.agent_id,
        "server": ctx.server,
        "tool": ctx.tool,
        "arguments": ctx.audit_arguments if ctx.audit_arguments is not None else ctx.arguments,
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


def call_events(ctx: CallContext) -> list[dict[str, Any]]:
    """Security events raised during the call, linked to its audit row."""
    events: list[dict[str, Any]] = ctx.extra.get(SECURITY_EVENTS_KEY, [])
    return [{**event, "audit_call_id": ctx.call_id} for event in events]


class AuditMiddleware(Middleware):
    """Writes every call (and its security events) to the store."""

    name = "audit"

    def __init__(self, store: GatewayStore, hub: AuditHub | None = None) -> None:
        self.store = store
        self.hub = hub

    async def on_request(self, ctx: CallContext) -> None:
        """Record the call as in progress (fails closed if the store is unreachable)."""
        await self.store.upsert_audit(audit_row(ctx))

    async def on_complete(self, ctx: CallContext) -> None:
        """Record the final status, reason, latencies and security events."""
        row = audit_row(ctx)
        await self.store.upsert_audit(row, call_events(ctx))
        if self.hub is not None:
            self.hub.publish(row)
