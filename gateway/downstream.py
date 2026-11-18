"""Southbound side: one long-lived MCP client session per downstream server."""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from mcp import Client, StdioServerParameters
from mcp.server.lowlevel import Server
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, Tool

from gateway.config import ServerConfig
from gateway.namespacing import namespace
from gateway.pipeline.errors import UnknownToolError

log = logging.getLogger(__name__)

# In-process servers are accepted for tests (``mcp.Client`` supports them natively).
ServerTarget = ServerConfig | Server[Any] | MCPServer


@dataclass(frozen=True)
class ToolRoute:
    """Where a public tool name goes."""

    server: str
    tool: Tool  # downstream definition (original name)


def _client_for(target: ServerTarget) -> Client:
    if isinstance(target, Server | MCPServer):
        return Client(target)
    if target.transport == "streamable_http":
        assert target.url is not None
        return Client(target.url, read_timeout_seconds=target.timeout_s)
    assert target.command is not None
    params = StdioServerParameters(command=target.command, args=target.args, cwd=target.cwd)
    return Client(params, read_timeout_seconds=target.timeout_s)


class DownstreamManager:
    """Connects to all downstream servers and routes namespaced calls to them.

    Use as an async context manager: connections open on enter and close on exit.
    """

    def __init__(self, targets: dict[str, ServerTarget]) -> None:
        self.targets = targets
        self.clients: dict[str, Client] = {}
        self.routes: dict[str, ToolRoute] = {}
        self._stack = AsyncExitStack()

    async def __aenter__(self) -> DownstreamManager:
        try:
            for name, target in self.targets.items():
                self.clients[name] = await self._stack.enter_async_context(_client_for(target))
                log.info("connected to downstream server %s", name)
            await self.refresh()
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()
        self.clients.clear()
        self.routes.clear()

    async def refresh(self) -> None:
        """Re-discover all tools (following pagination) and rebuild the routing table."""
        routes: dict[str, ToolRoute] = {}
        for server, client in self.clients.items():
            cursor: str | None = None
            while True:
                page = await client.list_tools(cursor=cursor, cache_mode="bypass")
                for tool in page.tools:
                    routes[namespace(server, tool.name)] = ToolRoute(server=server, tool=tool)
                cursor = page.next_cursor
                if not cursor:
                    break
        self.routes = routes

    def public_tools(self) -> list[Tool]:
        """All tools, renamed ``<server>__<tool>``; everything else is passed through unchanged."""
        return [route.tool.model_copy(update={"name": public}) for public, route in self.routes.items()]

    def route(self, public_name: str) -> ToolRoute:
        """Look up the downstream server and tool for a public name."""
        try:
            return self.routes[public_name]
        except KeyError:
            raise UnknownToolError(public_name) from None

    async def call(self, server: str, tool: str, arguments: dict[str, Any]) -> CallToolResult:
        """Call a tool on a downstream server and return its raw result."""
        client = self.clients.get(server)
        if client is None:
            raise UnknownToolError(f"{server}/{tool}")
        return await client.call_tool(tool, arguments)
