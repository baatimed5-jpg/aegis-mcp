"""Northbound side: the gateway seen by agents, as a low-level MCP server with dynamic tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from mcp.server import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.types import CallToolRequestParams, CallToolResult, ListToolsResult, PaginatedRequestParams

if TYPE_CHECKING:
    from gateway.app import Gateway

AgentResolver = Callable[[ServerRequestContext[Any]], str]

_RESERVED_META_PREFIX = "io.modelcontextprotocol/"


def user_meta(meta: object) -> dict[str, Any]:
    """Keep the caller's own ``_meta`` keys (e.g. the user question, D4), drop SDK-managed ones."""
    if not isinstance(meta, dict):
        return {}
    return {k: v for k, v in meta.items() if not k.startswith(_RESERVED_META_PREFIX) and k != "progressToken"}


def build_mcp_server(gateway: Gateway, resolve_agent: AgentResolver) -> Server[Any]:
    """Create the agent-facing MCP server.

    ``resolve_agent`` turns a request context into an agent id (stdio: fixed by ``--agent``;
    HTTP: the configured default until API-key auth arrives in Phase 3).
    """

    async def on_list_tools(
        ctx: ServerRequestContext[Any], params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        tools = await gateway.list_tools(resolve_agent(ctx), user_meta(ctx.meta))
        return ListToolsResult(tools=tools)

    async def on_call_tool(ctx: ServerRequestContext[Any], params: CallToolRequestParams) -> CallToolResult:
        return await gateway.call_tool(
            agent_id=resolve_agent(ctx),
            public_name=params.name,
            arguments=params.arguments or {},
            meta=user_meta(ctx.meta),
        )

    return Server(
        "aegis-gateway",
        version="0.1.0",
        title="AegisMCP Gateway",
        instructions="Tools from several MCP servers, namespaced as <server>__<tool>.",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )
