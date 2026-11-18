"""Northbound side: the gateway seen by agents, as a low-level MCP server with dynamic tools."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mcp.server import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.types import CallToolRequestParams, CallToolResult, ListToolsResult, PaginatedRequestParams

if TYPE_CHECKING:
    from gateway.app import Gateway


@dataclass(frozen=True)
class Identity:
    """Who is calling: ``agent_id`` is trusted only if ``authenticated``; otherwise the auth middleware
    maps ``credential`` (the API key) to an agent."""

    agent_id: str
    credential: str | None = None
    authenticated: bool = False


# A plain string means an identity fixed by the transport (stdio ``--agent``, tests).
AgentResolver = Callable[[ServerRequestContext[Any]], "Identity | str"]

_RESERVED_META_PREFIX = "io.modelcontextprotocol/"


def user_meta(meta: object) -> dict[str, Any]:
    """Keep the caller's own ``_meta`` keys (e.g. the user question, D4), drop SDK-managed ones."""
    if not isinstance(meta, dict):
        return {}
    return {k: v for k, v in meta.items() if not k.startswith(_RESERVED_META_PREFIX) and k != "progressToken"}


def resolve(resolver: AgentResolver, ctx: ServerRequestContext[Any]) -> Identity:
    """Run ``resolver`` and normalize its answer to an ``Identity``."""
    found = resolver(ctx)
    return Identity(agent_id=found, authenticated=True) if isinstance(found, str) else found


def bearer_identity(default_agent: str) -> AgentResolver:
    """HTTP resolver: the ``Authorization: Bearer <key>`` header is checked later by the auth middleware."""

    def resolver(ctx: ServerRequestContext[Any]) -> Identity:
        headers = getattr(ctx.request, "headers", None)
        header = headers.get("authorization", "") if headers is not None else ""
        scheme, _, token = str(header).partition(" ")
        credential = token.strip() if scheme.lower() == "bearer" and token.strip() else None
        return Identity(agent_id=default_agent, credential=credential, authenticated=False)

    return resolver


def build_mcp_server(gateway: Gateway, resolve_agent: AgentResolver) -> Server[Any]:
    """Create the agent-facing MCP server.

    ``resolve_agent`` turns a request context into an identity (stdio: fixed by ``--agent``;
    HTTP: the Bearer API key, checked by the auth middleware).
    """

    async def on_list_tools(
        ctx: ServerRequestContext[Any], params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        who = resolve(resolve_agent, ctx)
        tools = await gateway.list_tools(
            who.agent_id, user_meta(ctx.meta), credential=who.credential, authenticated=who.authenticated
        )
        return ListToolsResult(tools=tools)

    async def on_call_tool(ctx: ServerRequestContext[Any], params: CallToolRequestParams) -> CallToolResult:
        who = resolve(resolve_agent, ctx)
        return await gateway.call_tool(
            agent_id=who.agent_id,
            public_name=params.name,
            arguments=params.arguments or {},
            meta=user_meta(ctx.meta),
            credential=who.credential,
            authenticated=who.authenticated,
        )

    return Server(
        "aegis-gateway",
        version="0.1.0",
        title="AegisMCP Gateway",
        instructions="Tools from several MCP servers, namespaced as <server>__<tool>.",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )
