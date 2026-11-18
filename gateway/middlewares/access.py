"""Access control middlewares: API-key authentication (D5) and policies + approvals."""

from __future__ import annotations

import hmac
import logging

from mcp.types import Tool

from gateway.approvals import ApprovalService
from gateway.pipeline.base import Middleware
from gateway.pipeline.context import CallContext, ListContext
from gateway.pipeline.errors import PipelineDenied
from gateway.policy import PolicySet

log = logging.getLogger(__name__)

UNAUTHENTICATED = "unauthenticated"


class AuthMiddleware(Middleware):
    """Maps the caller's API key to an agent id; unknown or missing keys are denied.

    Identities already established by the transport (stdio ``--agent``) are kept as they are.
    Keys are compared in constant time and never logged.
    """

    name = "auth"

    def __init__(self, keys: dict[str, str]) -> None:
        """``keys`` maps agent id -> API key."""
        self._keys = [(agent, key.encode()) for agent, key in keys.items() if key]

    def identify(self, credential: str | None) -> str | None:
        """Return the agent id owning ``credential``, or ``None``."""
        if not credential:
            return None
        given = credential.encode()
        found: str | None = None
        for agent, key in self._keys:  # check every key, so timing does not reveal which one matched
            if hmac.compare_digest(given, key):
                found = agent
        return found

    async def on_list_tools(self, ctx: ListContext, tools: list[Tool]) -> list[Tool]:
        """Unauthenticated callers see no tools."""
        if ctx.authenticated:
            return tools
        agent = self.identify(ctx.credential)
        if agent is None:
            ctx.agent_id = UNAUTHENTICATED
            return []
        ctx.agent_id, ctx.authenticated = agent, True
        return tools

    async def on_request(self, ctx: CallContext) -> None:
        """Deny calls without a valid API key."""
        if ctx.authenticated:
            return
        agent = self.identify(ctx.credential)
        if agent is None:
            ctx.agent_id = UNAUTHENTICATED
            reason = "missing API key" if not ctx.credential else "invalid API key"
            raise PipelineDenied(reason, code="unauthorized", status="denied")
        ctx.agent_id, ctx.authenticated = agent, True


class PolicyMiddleware(Middleware):
    """Applies ``PolicySet``: hides tools an agent may not use, denies their calls, and sends
    ``require_approval`` calls to the approval queue."""

    name = "policy"

    def __init__(self, policies: PolicySet, approvals: ApprovalService | None) -> None:
        self.policies = policies
        self.approvals = approvals

    async def on_list_tools(self, ctx: ListContext, tools: list[Tool]) -> list[Tool]:
        """Keep only tools the agent may call (directly or with approval)."""
        return [t for t in tools if self.policies.decide(ctx.agent_id, t.name) != "deny"]

    async def on_request(self, ctx: CallContext) -> None:
        """Deny, pass, or wait for a human decision."""
        decision = self.policies.decide(ctx.agent_id, ctx.public_name)
        if decision == "deny":
            raise PipelineDenied(
                f"agent {ctx.agent_id!r} may not call {ctx.public_name!r}", code="policy", status="denied"
            )
        if decision == "approval":
            if self.approvals is None:
                raise PipelineDenied("approval required but no approval queue", code="approval_unavailable")
            await self.approvals.request(ctx)
