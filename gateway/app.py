"""Gateway core (downstream + pipeline) and the HTTP / stdio entry points."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, Tool

from gateway.config import GatewayConfig
from gateway.downstream import DownstreamManager, ServerTarget
from gateway.mcp_facade import build_mcp_server
from gateway.middlewares.registry import build_pipeline
from gateway.namespacing import split
from gateway.pipeline.context import CallContext, ListContext
from gateway.pipeline.runner import Pipeline

log = logging.getLogger(__name__)


class Gateway:
    """Ties the downstream connections and the middleware pipeline together.

    Use as an async context manager (``async with Gateway(config): ...``).
    """

    def __init__(
        self,
        config: GatewayConfig,
        *,
        targets: dict[str, ServerTarget] | None = None,
        pipeline: Pipeline | None = None,
    ) -> None:
        self.config = config
        self.downstream = DownstreamManager(targets if targets is not None else dict(config.enabled_servers))
        self.pipeline = pipeline if pipeline is not None else build_pipeline(config)

    async def __aenter__(self) -> Gateway:
        await self.pipeline.startup()
        try:
            await self.downstream.__aenter__()
        except BaseException:
            await self.pipeline.shutdown()
            raise
        log.info(
            "gateway ready: %d tools from %s", len(self.downstream.routes), list(self.downstream.clients)
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        try:
            await self.downstream.__aexit__(*exc)
        finally:
            await self.pipeline.shutdown()

    async def list_tools(self, agent_id: str, meta: dict[str, Any] | None = None) -> list[Tool]:
        """Aggregated, namespaced tool list after the ``on_list_tools`` hooks."""
        ctx = ListContext(agent_id=agent_id, meta=meta or {})
        return await self.pipeline.list_tools(ctx, self.downstream.public_tools())

    async def call_tool(
        self, agent_id: str, public_name: str, arguments: dict[str, Any], meta: dict[str, Any] | None = None
    ) -> CallToolResult:
        """Run one call through the pipeline. Unknown names are still audited, then rejected."""
        try:
            server, tool = split(public_name)
        except ValueError:
            server, tool = "", public_name
        ctx = CallContext(
            agent_id=agent_id, public_name=public_name, server=server, tool=tool, arguments=arguments,
            meta=meta or {},
        )  # fmt: skip

        async def downstream(c: CallContext) -> CallToolResult:
            route = self.downstream.route(c.public_name)
            return await self.downstream.call(route.server, route.tool.name, c.arguments)

        return await self.pipeline.run(ctx, downstream)


def create_http_app(gateway: Gateway, *, host: str = "127.0.0.1") -> FastAPI:
    """FastAPI app: MCP Streamable HTTP at ``/mcp`` plus ``/healthz`` (REST API comes in later phases)."""
    default_agent = gateway.config.default_agent
    mcp_server = build_mcp_server(gateway, lambda _ctx: default_agent)
    mcp_app = mcp_server.streamable_http_app(host=host)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        async with gateway, mcp_server.session_manager.run():
            yield

    app = FastAPI(title="AegisMCP Gateway", version="0.1.0", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        """Liveness plus the connected downstream servers."""
        return {
            "status": "ok",
            "servers": sorted(gateway.downstream.clients),
            "tools": len(gateway.downstream.routes),
        }

    app.mount("/", mcp_app)
    return app


async def run_stdio(gateway: Gateway, agent_id: str) -> None:
    """Serve one agent over stdin/stdout; the agent identity is fixed by the launcher."""
    mcp_server = build_mcp_server(gateway, lambda _ctx: agent_id)
    async with gateway, stdio_server() as (read_stream, write_stream):
        await mcp_server.run(read_stream, write_stream, mcp_server.create_initialization_options())
