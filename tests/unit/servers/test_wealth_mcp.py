"""MCP-level tests: the tools work through a real (in-process) MCP client session."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest
from mcp import Client

from servers.wealth.server import build_server

DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
TZ_SUFFIX = re.compile(r"[+-]\d{2}:\d{2}$")

EXPECTED_TOOLS = {
    "get_client",
    "list_portfolios",
    "get_holdings",
    "get_transactions",
    "get_portfolio_performance",
    "transfer_funds",
}


async def test_list_tools_has_six_tools() -> None:
    async with Client(build_server()) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == EXPECTED_TOOLS
    annotations = tools["transfer_funds"].annotations
    assert annotations is not None
    assert annotations.destructive_hint is True
    assert tools["get_holdings"].output_schema is not None


async def test_call_tool_returns_structured_content() -> None:
    async with Client(build_server()) as client:
        res = await client.call_tool("get_holdings", {"portfolio_id": "PF-00001"})
    assert not res.is_error
    assert isinstance(res.structured_content, dict)
    assert res.structured_content["portfolio_id"] == "PF-00001"
    assert len(res.structured_content["holdings"]) >= 12


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("get_client", {"client_id": "CL-0001"}),
        ("list_portfolios", {"client_id": "CL-0001"}),
        ("get_holdings", {"portfolio_id": "PF-00001"}),
        ("get_transactions", {"portfolio_id": "PF-00001"}),
    ],
)
async def test_datetimes_are_rfc3339(tool: str, args: dict[str, str]) -> None:
    """JSON Schema 'date-time' needs a UTC offset; strict clients (MCP Inspector) reject naive ones."""
    async with Client(build_server()) as client:
        res = await client.call_tool(tool, args)
    stamps = [s for s in _strings(res.structured_content) if DATETIME.match(s)]
    assert stamps
    assert all(s.endswith("Z") or TZ_SUFFIX.search(s) for s in stamps), stamps[:3]


async def test_call_tool_error_is_reported() -> None:
    async with Client(build_server()) as client:
        res = await client.call_tool("get_client", {"client_id": "CL-9999"})
    assert res.is_error
