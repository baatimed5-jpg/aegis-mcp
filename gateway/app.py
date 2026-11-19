"""Gateway core (downstream + pipeline) and the HTTP / stdio entry points."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations

from gateway.api.approvals import approvals_router
from gateway.api.dashboard import dashboard_router
from gateway.cache import cache_key, is_cacheable
from gateway.config import GatewayConfig
from gateway.downstream import DownstreamManager, ServerTarget
from gateway.evidence import pick
from gateway.mcp_facade import bearer_identity, build_mcp_server
from gateway.middlewares.registry import build_pipeline
from gateway.namespacing import split
from gateway.pipeline.context import CallContext, ListContext
from gateway.pipeline.errors import UnknownToolError
from gateway.pipeline.runner import Pipeline
from gateway.services import Services

log = logging.getLogger(__name__)

RUN_ID_META_KEY = "aegis/run_id"
"""Optional ``_meta`` key: id of an ``experiment_run`` row the call belongs to (evaluation only)."""


FETCH_OMITTED = "aegis__fetch_omitted"
VirtualHandler = Callable[[dict[str, Any]], Awaitable[CallToolResult]]


def fetch_omitted_tool() -> Tool:
    """Virtual tool of the gateway itself: get back fields the optimizer removed (evidence recovery)."""
    return Tool(
        name=FETCH_OMITTED,
        description=(
            "Get the fields that AegisMCP removed from an earlier tool result to save tokens. "
            "Use the 'ref' from that result's _omitted section; optionally list the fields you need."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "The _omitted.ref value of the earlier result"},
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional field paths",
                },
            },
            "required": ["ref"],
        },
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )


def _json_result(data: dict[str, Any], *, error: bool = False) -> CallToolResult:
    text = json.dumps(data, ensure_ascii=False, default=str)
    return CallToolResult(
        content=[TextContent(type="text", text=text)], structured_content=data, is_error=error
    )


def run_id_from_meta(meta: dict[str, Any]) -> uuid.UUID | None:
    """Parse the evaluation run id sent by the agent, ignoring malformed values."""
    raw = meta.get(RUN_ID_META_KEY)
    if not isinstance(raw, str):
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


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
        services: Services | None = None,
    ) -> None:
        self.config = config
        self.services = services if services is not None else Services(config)
        self.downstream = DownstreamManager(targets if targets is not None else dict(config.enabled_servers))
        self.pipeline = pipeline if pipeline is not None else build_pipeline(config, self.services)
        self.virtual: dict[str, tuple[Tool, VirtualHandler]] = {}
        optimizer_cfg = config.middlewares.get("optimizer", {})
        if "optimizer" in config.pipeline.response and optimizer_cfg.get("evidence_recovery", True):
            self.virtual[FETCH_OMITTED] = (fetch_omitted_tool(), self._fetch_omitted)

    async def _fetch_omitted(self, arguments: dict[str, Any]) -> CallToolResult:
        """Handler of ``aegis__fetch_omitted``: return stored fields of an earlier result."""
        ref = str(arguments.get("ref", ""))
        data = await self.services.evidence.get(ref)
        if data is None:
            return _json_result({"error": f"unknown or expired ref: {ref}"}, error=True)
        fields = arguments.get("fields")
        return _json_result(pick(data, fields if isinstance(fields, list) else None))

    async def __aenter__(self) -> Gateway:
        try:
            await self.pipeline.startup()
            await self.downstream.__aenter__()
        except BaseException:
            await self.pipeline.shutdown()
            await self.services.close()
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
            await self.services.close()

    async def list_tools(
        self,
        agent_id: str,
        meta: dict[str, Any] | None = None,
        *,
        credential: str | None = None,
        authenticated: bool = True,
    ) -> list[Tool]:
        """Aggregated, namespaced tool list after the ``on_list_tools`` hooks.

        ``authenticated=True`` means ``agent_id`` is already trusted (stdio, tests); HTTP callers pass
        their API key as ``credential`` with ``authenticated=False``.
        """
        ctx = ListContext(
            agent_id=agent_id, meta=meta or {}, credential=credential, authenticated=authenticated
        )
        tools = [*self.downstream.public_tools(), *(tool for tool, _ in self.virtual.values())]
        return await self.pipeline.list_tools(ctx, tools)

    async def call_tool(
        self,
        agent_id: str,
        public_name: str,
        arguments: dict[str, Any],
        meta: dict[str, Any] | None = None,
        *,
        credential: str | None = None,
        authenticated: bool = True,
    ) -> CallToolResult:
        """Run one call through the pipeline. Unknown names are still audited, then rejected."""
        try:
            server, tool = split(public_name)
        except ValueError:
            server, tool = "", public_name
        ctx = CallContext(
            agent_id=agent_id, public_name=public_name, server=server, tool=tool, arguments=arguments,
            meta=meta or {}, credential=credential, authenticated=authenticated,
            run_id=run_id_from_meta(meta or {}),
        )  # fmt: skip
        route = self.downstream.routes.get(public_name)
        ctx.tool_def = route.tool if route is not None else None
        virtual = self.virtual.get(public_name)
        if virtual is not None:
            ctx.tool_def = virtual[0]

        async def downstream(c: CallContext) -> CallToolResult:
            if virtual is not None:
                return await virtual[1](c.arguments)
            if c.public_name not in self.downstream.routes:
                raise UnknownToolError(c.public_name)
            route = self.downstream.route(c.public_name)
            cache = self.config.cache
            if not (cache.enabled and is_cacheable(route.tool)):
                return await self.downstream.call(route.server, route.tool.name, c.arguments)
            key = cache_key(route.server, route.tool.name, c.arguments)
            cached = await self.services.cache.get(key)
            if cached is not None:
                c.cache_hit = True
                return cached
            result = await self.downstream.call(route.server, route.tool.name, c.arguments)
            if not result.is_error:
                await self.services.cache.set(key, result, cache.ttl_s)
            return result

        return await self.pipeline.run(ctx, downstream)


def create_http_app(gateway: Gateway, *, host: str = "127.0.0.1") -> FastAPI:
    """FastAPI app: MCP Streamable HTTP at ``/mcp``, ``/healthz`` and the admin REST API under ``/api``."""
    mcp_server = build_mcp_server(gateway, bearer_identity(gateway.config.default_agent))
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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=gateway.config.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(approvals_router(gateway))
    app.include_router(dashboard_router(gateway))
    app.mount("/", mcp_app)
    return app


async def run_stdio(gateway: Gateway, agent_id: str) -> None:
    """Serve one agent over stdin/stdout; the agent identity is fixed by the launcher."""
    mcp_server = build_mcp_server(gateway, lambda _ctx: agent_id)
    async with gateway, stdio_server() as (read_stream, write_stream):
        await mcp_server.run(read_stream, write_stream, mcp_server.create_initialization_options())
