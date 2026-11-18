"""Pipeline runner: request middlewares → downstream call → response middlewares → completion hooks."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from mcp.types import CallToolResult, TextContent, Tool

from gateway.pipeline.base import Middleware
from gateway.pipeline.context import CallContext, ListContext
from gateway.pipeline.errors import PipelineDenied, UnknownToolError

log = logging.getLogger(__name__)

DownstreamCall = Callable[[CallContext], Awaitable[CallToolResult]]


def error_result(message: str) -> CallToolResult:
    """Build a tool-level error the agent's LLM can read (D2)."""
    return CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)


class Pipeline:
    """Runs one call through the configured middlewares.

    ``request`` and ``response`` are independent ordered lists (D1). A middleware may appear in
    both; its ``on_complete`` and lifecycle hooks still run only once.
    """

    def __init__(self, request: list[Middleware], response: list[Middleware]) -> None:
        self.request = list(request)
        self.response = list(response)
        unique: dict[int, Middleware] = {}
        for mw in [*self.request, *self.response]:
            unique.setdefault(id(mw), mw)
        self.all: list[Middleware] = list(unique.values())

    async def startup(self) -> None:
        """Start every middleware."""
        for mw in self.all:
            await mw.startup()

    async def shutdown(self) -> None:
        """Stop every middleware (reverse order), logging but not raising errors."""
        for mw in reversed(self.all):
            try:
                await mw.shutdown()
            except Exception:
                log.exception("middleware %s failed to shut down", mw.name)

    async def list_tools(self, ctx: ListContext, tools: list[Tool]) -> list[Tool]:
        """Apply every middleware's ``on_list_tools`` in request order."""
        for mw in self.all:
            tools = await mw.on_list_tools(ctx, tools)
        return tools

    async def run(self, ctx: CallContext, downstream: DownstreamCall) -> CallToolResult:
        """Run the call and always return a ``CallToolResult`` (never raises for call failures)."""
        try:
            for mw in self.request:
                await mw.on_request(ctx)
            t0 = time.perf_counter()
            try:
                ctx.result = await downstream(ctx)
            finally:
                ctx.latency_downstream_ms = (time.perf_counter() - t0) * 1000
            if ctx.result.is_error:
                ctx.status = "error"
                ctx.reason = "downstream tool returned an error"
            for mw in self.response:
                await mw.on_response(ctx)
            if ctx.status == "in_progress":
                ctx.status = "approved" if ctx.approved else "allowed"
        except PipelineDenied as exc:
            ctx.status = exc.status
            ctx.reason = f"{exc.code}: {exc.reason}"
            ctx.result = error_result(f"Request {exc.status} by gateway: {exc.reason}")
        except UnknownToolError as exc:
            ctx.status = "error"
            ctx.reason = f"unknown_tool: {exc}"
            ctx.result = error_result(f"Unknown tool: {ctx.public_name}")
        except Exception as exc:
            # Fail closed: any unexpected failure (middleware or downstream) ends the call.
            log.exception("call %s (%s) failed", ctx.call_id, ctx.public_name)
            ctx.status = "error"
            ctx.reason = f"{type(exc).__name__}: {exc}"
            ctx.result = error_result(
                f"Gateway error while calling {ctx.public_name} ({type(exc).__name__})."
            )
        finally:
            ctx.latency_total_ms = (time.perf_counter() - ctx.started) * 1000
            for mw in self.all:
                try:
                    await mw.on_complete(ctx)
                except Exception:
                    log.exception("on_complete of %s failed for call %s", mw.name, ctx.call_id)
        assert ctx.result is not None
        return ctx.result
