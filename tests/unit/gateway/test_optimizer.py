"""Unit tests: rule pruning, compact serialization, evidence recovery, cache and token metrics (Phase 5)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from gateway.app import FETCH_OMITTED, Gateway
from gateway.cache import MemoryResultCache, cache_key
from gateway.config import GatewayConfig
from gateway.evidence import MemoryEvidenceStore, pick
from gateway.middlewares.audit import AuditMiddleware
from gateway.middlewares.optimizer import (
    MetricsMiddleware,
    MetricsSettings,
    OptimizerMiddleware,
    OptimizerSettings,
    RulePruner,
    relax_schema,
)
from gateway.optimizer.rules import PruningRules, load_rules, prune
from gateway.optimizer.serialize import render, tabulate
from gateway.pipeline.context import CallContext
from gateway.pipeline.runner import Pipeline
from gateway.services import Services
from gateway.store import MemoryStore
from gateway.tokens import count_tokens
from servers.wealth.server import build_server

ROOT = Path(__file__).resolve().parents[3]


def test_prune_paths_and_lists() -> None:
    data = {"a": 1, "b": {"c": 2, "d": 3}, "items": [{"x": 1, "y": 2}, {"x": 3, "y": 4}]}
    pruned, removed = prune(data, ["b.c", "items[].y", "missing.path"])
    assert pruned == {"a": 1, "b": {"d": 3}, "items": [{"x": 1}, {"x": 3}]}
    assert removed == ["b.c", "items[].y"]
    assert data["b"] == {"c": 2, "d": 3}  # input untouched


def test_tabulate_is_lossless() -> None:
    data = {
        "rows": [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}],
        "single": [{"a": 1}],
        "mixed": [{"a": 1}, {"b": 2}],
    }
    table = tabulate(data)
    assert table["rows"] == {"columns": ["a", "b"], "rows": [[1, "x"], [2, "y"]]}
    assert table["single"] == [{"a": 1}]
    assert table["mixed"] == [{"a": 1}, {"b": 2}]
    rebuilt = [dict(zip(table["rows"]["columns"], r, strict=True)) for r in table["rows"]["rows"]]
    assert rebuilt == data["rows"]
    assert count_tokens(render(data, "compact")) < count_tokens(render(data, "json"))


def test_repo_rules_load_and_cover_every_tool() -> None:
    rules = load_rules(ROOT / "config" / "pruning_rules.yaml")
    assert set(rules.tools) >= {"get_client", "list_portfolios", "get_holdings", "get_transactions"}


def _ctx(tool: str, result: CallToolResult) -> CallContext:
    ctx = CallContext(agent_id="a", public_name=f"wealth__{tool}", server="wealth", tool=tool, arguments={})
    ctx.result = result
    return ctx


def _result(data: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(data, indent=2))], structured_content=data
    )


async def test_optimizer_prunes_records_evidence_and_metrics() -> None:
    evidence = MemoryEvidenceStore()
    rules = PruningRules(tools={"t": ["big", "items[].noise"]})
    opt = OptimizerMiddleware(OptimizerSettings(serialization="compact"), RulePruner(rules), evidence)
    data = {"keep": 1, "big": "x" * 500, "items": [{"v": i, "noise": "n" * 50} for i in range(5)]}
    ctx = _ctx("t", _result(data))
    await opt.on_response(ctx)
    await MetricsMiddleware(MetricsSettings(input_price_per_mtok_usd=1.0)).on_response(ctx)
    assert ctx.result is not None
    out = ctx.result.structured_content
    assert out is not None
    assert "big" not in out
    assert out["_omitted"]["fields"] == ["big", "items[].noise"]
    stored = await evidence.get(out["_omitted"]["ref"])
    assert stored == data
    assert ctx.extra["tokens_after"] < ctx.extra["tokens_before"]
    assert ctx.extra["cost_saved_usd"] == pytest.approx(
        (ctx.extra["tokens_before"] - ctx.extra["tokens_after"]) / 1e6
    )
    text = ctx.result.content[0]
    assert isinstance(text, TextContent)
    assert '"columns":["v"]' in text.text


async def test_optimizer_skips_errors_and_unchanged_results() -> None:
    opt = OptimizerMiddleware(OptimizerSettings(), RulePruner(PruningRules()), None)
    err = CallToolResult(content=[TextContent(type="text", text="boom")], is_error=True)
    ctx = _ctx("t", err)
    await opt.on_response(ctx)
    assert ctx.result is err
    ok = _result({"a": 1})
    ctx = _ctx("t", ok)
    await opt.on_response(ctx)
    assert ctx.result is ok  # json + nothing pruned: server text kept


def test_pick_fields() -> None:
    data = {"a": 1, "holdings": [{"x": 1}], "b": 2}
    assert pick(data, None) == data
    assert pick(data, ["holdings[].instrument", "b"]) == {"holdings": [{"x": 1}], "b": 2}


async def test_memory_cache_roundtrip() -> None:
    cache = MemoryResultCache()
    res = _result({"a": 1})
    key = cache_key("s", "t", {"x": 1})
    assert key == cache_key("s", "t", {"x": 1})
    assert key != cache_key("s", "t", {"x": 2})
    assert await cache.get(key) is None
    await cache.set(key, res, 60)
    hit = await cache.get(key)
    assert hit is not None
    assert hit.structured_content == {"a": 1}


CONFIG = GatewayConfig.model_validate(
    {
        "servers": {"wealth": {"transport": "streamable_http", "url": "http://unused/mcp"}},
        "cache": {"enabled": True, "ttl_s": 60},
        "pipeline": {"request": ["audit"], "response": ["optimizer", "metrics"]},
        "middlewares": {"optimizer": {"rules_file": str(ROOT / "config" / "pruning_rules.yaml")}},
    }
)


@asynccontextmanager
async def optimized_gateway(store: MemoryStore) -> AsyncIterator[Gateway]:
    services = Services(CONFIG, store=store)
    rules = load_rules(ROOT / "config" / "pruning_rules.yaml")
    opt = OptimizerMiddleware(
        OptimizerSettings(serialization="compact"), RulePruner(rules), services.evidence
    )
    pipeline = Pipeline(
        request=[AuditMiddleware(store)], response=[opt, MetricsMiddleware(MetricsSettings())]
    )
    async with Gateway(
        CONFIG, targets={"wealth": build_server()}, pipeline=pipeline, services=services
    ) as gw:
        yield gw


async def test_gateway_fetch_omitted_cache_and_audit_tokens() -> None:
    store = MemoryStore()
    async with optimized_gateway(store) as gw:
        tools = {t.name for t in await gw.list_tools("assistant")}
        assert FETCH_OMITTED in tools
        first = await gw.call_tool("assistant", "wealth__get_holdings", {"portfolio_id": "PF-00001"})
        second = await gw.call_tool("assistant", "wealth__get_holdings", {"portfolio_id": "PF-00001"})
        assert first.structured_content is not None
        ref = first.structured_content["_omitted"]["ref"]
        fetched = await gw.call_tool("assistant", FETCH_OMITTED, {"ref": ref, "fields": ["holdings"]})
        missing = await gw.call_tool("assistant", FETCH_OMITTED, {"ref": "nope"})
        transfer_args = {"from_portfolio_id": "PF-00001", "to_portfolio_id": "PF-00002", "amount": 1.0}
        await gw.call_tool("assistant", "wealth__transfer_funds", transfer_args)
        await gw.call_tool("assistant", "wealth__transfer_funds", transfer_args)
    assert second.structured_content is not None
    assert "instrument" not in second.structured_content["holdings"][0]
    assert fetched.structured_content is not None
    assert "instrument" in fetched.structured_content["holdings"][0]
    assert missing.is_error
    rows = list(store.audit.values())
    holdings = [r for r in rows if r["tool"] == "get_holdings"]
    assert [r["cache_hit"] for r in holdings] == [False, True]
    assert all(r["tokens_after"] < r["tokens_before"] for r in holdings)
    transfers = [r for r in rows if r["tool"] == "transfer_funds"]
    assert [r["cache_hit"] for r in transfers] == [False, False]  # not read-only: never cached


def test_relax_schema_drops_required_everywhere() -> None:
    schema = {
        "type": "object",
        "required": ["a"],
        "properties": {"a": {"type": "array", "items": {"type": "object", "required": ["x"]}}},
        "$defs": {"D": {"required": ["y"], "properties": {"y": {"type": "string"}}}},
    }
    relaxed = relax_schema(schema)
    assert "required" not in json.dumps(relaxed)
    assert relaxed["properties"]["a"]["items"]["type"] == "object"


async def test_pruned_results_validate_against_advertised_schema() -> None:
    """An MCP client validates structuredContent against outputSchema: pruned results must pass."""
    import jsonschema

    store = MemoryStore()
    async with optimized_gateway(store) as gw:
        tools = {t.name: t for t in await gw.list_tools("assistant")}
        res = await gw.call_tool("assistant", "wealth__get_holdings", {"portfolio_id": "PF-00001"})
    schema = tools["wealth__get_holdings"].output_schema
    assert schema is not None
    jsonschema.validate(res.structured_content, schema)
