"""Middleware base class (D1): optional hooks, ordered by two config lists."""

from __future__ import annotations

from mcp.types import Tool

from gateway.pipeline.context import CallContext, ListContext


class Middleware:
    """Base class for gateway middlewares. Every hook is optional (default: no-op).

    - ``on_list_tools``: filter or rewrite the aggregated tool list.
    - ``on_request``: inspect/modify the call before the downstream call; raise
      ``PipelineDenied`` to stop it.
    - ``on_response``: inspect/modify ``ctx.result`` after the downstream call.
    - ``on_complete``: always runs, whatever the outcome (D2), e.g. for auditing.
    """

    name: str = "middleware"

    async def on_list_tools(self, ctx: ListContext, tools: list[Tool]) -> list[Tool]:
        """Return the (possibly filtered) tool list."""
        return tools

    async def on_request(self, ctx: CallContext) -> None:
        """Run before the downstream call."""

    async def on_response(self, ctx: CallContext) -> None:
        """Run after the downstream call returned a result."""

    async def on_complete(self, ctx: CallContext) -> None:
        """Run once at the end of every call."""

    async def startup(self) -> None:
        """Acquire resources (DB connections, models) when the gateway starts."""

    async def shutdown(self) -> None:
        """Release resources when the gateway stops."""
