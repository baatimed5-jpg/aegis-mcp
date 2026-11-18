"""Unit tests: agent → MCP facade → gateway → wealth server, all in-process (no network, no DB)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from mcp import Client
from mcp.types import CallToolResult

from gateway.app import Gateway
from gateway.config import GatewayConfig
from gateway.mcp_facade import build_mcp_server, user_meta
from gateway.middlewares.audit import AuditMiddleware, audit_row
from gateway.pipeline.context import CallContext
from gateway.pipeline.runner import Pipeline
from gateway.services import Services
from gateway.store import MemoryStore
from servers.wealth.server import build_server

CONFIG = GatewayConfig.model_validate(
    {"servers": {"wealth": {"transport": "streamable_http", "url": "http://unused/mcp"}}}
)

CALLS: list[tuple[str, dict[str, Any]]] = [
    ("get_client", {"client_id": "CL-0001"}),
    ("list_portfolios", {"client_id": "CL-0002"}),
    ("get_holdings", {"portfolio_id": "PF-00001"}),
    ("get_transactions", {"portfolio_id": "PF-00003", "tx_type": "buy", "limit": 5}),
    ("get_portfolio_performance", {"portfolio_id": "PF-00002", "period": "6M"}),
    ("transfer_funds", {"from_portfolio_id": "PF-00001", "to_portfolio_id": "PF-00002", "amount": 1.0}),
    ("get_holdings", {"portfolio_id": "PF-99999"}),  # downstream tool error
]


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@asynccontextmanager
async def running(store: MemoryStore) -> AsyncIterator[Gateway]:
    """Open the gateway inside the test's own task (anyio cancel scopes must exit where entered)."""
    pipeline = Pipeline(request=[AuditMiddleware(store)], response=[])
    async with Gateway(CONFIG, targets={"wealth": build_server()}, pipeline=pipeline) as gw:
        yield gw


def comparable(res: CallToolResult) -> dict[str, Any]:
    return {"content": res.model_dump()["content"], "structured": res.structured_content, "err": res.is_error}


async def test_tools_are_namespaced(store: MemoryStore) -> None:
    async with running(store) as gateway, Client(build_server()) as direct:
        direct_tools = {t.name: t for t in (await direct.list_tools()).tools}
        async with Client(build_mcp_server(gateway, lambda _c: "assistant")) as agent:
            tools = (await agent.list_tools()).tools
    assert sorted(t.name for t in tools) == sorted(f"wealth__{n}" for n in direct_tools)
    for t in tools:
        original = direct_tools[t.name.removeprefix("wealth__")]
        assert t.description == original.description
        assert t.input_schema == original.input_schema
        assert t.output_schema == original.output_schema
        assert t.annotations == original.annotations


async def test_results_identical_to_direct(store: MemoryStore) -> None:
    async with (
        running(store) as gateway,
        Client(build_server()) as direct,
        Client(build_mcp_server(gateway, lambda _c: "assistant")) as agent,
    ):
        for tool, args in CALLS:
            expected = await direct.call_tool(tool, args)
            got = await agent.call_tool(f"wealth__{tool}", args)
            assert comparable(got) == comparable(expected), tool
    rows = list(store.audit.values())
    assert len(rows) == len(CALLS)
    assert [(r["tool"], r["arguments"]) for r in rows] == CALLS
    assert {r["agent_id"] for r in rows} == {"assistant"}
    assert [r["status"] for r in rows] == ["allowed"] * 6 + ["error"]
    assert all(r["latency_total_ms"] is not None and r["latency_downstream_ms"] is not None for r in rows)
    # each call: one in_progress write before the downstream call, then the final write
    assert store.audit_writes[:2] == ["in_progress", "allowed"]


async def test_unknown_tool_is_audited(store: MemoryStore) -> None:
    async with running(store) as gateway:
        for name in ("wealth__nope", "other__get_holdings", "no_namespace"):
            res = await gateway.call_tool("a", name, {})
            assert res.is_error
    rows = list(store.audit.values())
    assert [r["status"] for r in rows] == ["error"] * 3
    assert rows[1]["server"] == "other"
    assert rows[2]["server"] == ""
    assert all(r["reason"].startswith("unknown_tool") for r in rows)


async def test_audit_store_closed_on_exit(store: MemoryStore) -> None:
    pipeline = Pipeline(request=[AuditMiddleware(store)], response=[])
    services = Services(CONFIG, store=store)
    async with Gateway(CONFIG, targets={"wealth": build_server()}, pipeline=pipeline, services=services):
        pass
    assert store.closed


async def test_audit_failure_fails_closed() -> None:
    class BrokenStore(MemoryStore):
        async def upsert_audit(self, row: dict[str, Any], events: list[dict[str, Any]] | None = None) -> None:
            raise ConnectionError("db down")

    pipeline = Pipeline(request=[AuditMiddleware(BrokenStore())], response=[])
    async with Gateway(CONFIG, targets={"wealth": build_server()}, pipeline=pipeline) as gw:
        res = await gw.call_tool("a", "wealth__transfer_funds", {"from_portfolio_id": "PF-00001"})
    assert res.is_error


def test_user_meta_drops_sdk_keys() -> None:
    meta = {"io.modelcontextprotocol/clientInfo": {}, "progressToken": 1, "aegis/question": "q"}
    assert user_meta(meta) == {"aegis/question": "q"}
    assert user_meta(None) == {}


def test_audit_row_reads_extra_metrics() -> None:
    ctx = CallContext(agent_id="a", public_name="s__t", server="s", tool="t", arguments={})
    ctx.extra.update(tokens_before=10, tokens_after=4, pii_entities=2)
    row = audit_row(ctx)
    assert (row["tokens_before"], row["tokens_after"], row["pii_entities"]) == (10, 4, 2)
    assert row["injection_score"] is None
