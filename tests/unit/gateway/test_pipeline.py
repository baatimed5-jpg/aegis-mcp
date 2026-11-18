"""Unit tests: pipeline runner ordering, denials, failures and completion hooks."""

from __future__ import annotations

import pytest
from mcp.types import CallToolResult, TextContent, Tool

from gateway.config import GatewayConfig
from gateway.middlewares.registry import build_pipeline
from gateway.pipeline.base import Middleware
from gateway.pipeline.context import CallContext, ListContext
from gateway.pipeline.errors import PipelineDenied, UnknownToolError
from gateway.pipeline.runner import Pipeline
from gateway.services import Services


class Recorder(Middleware):
    """Appends ``<name>.<hook>`` to a shared log; optionally raises in one hook."""

    def __init__(self, name: str, log: list[str], fail_on: str | None = None, exc: Exception | None = None):
        self.name = name
        self.log = log
        self.fail_on = fail_on
        self.exc = exc or PipelineDenied("nope", code="test")

    def _hit(self, hook: str) -> None:
        self.log.append(f"{self.name}.{hook}")
        if hook == self.fail_on:
            raise self.exc

    async def on_request(self, ctx: CallContext) -> None:
        self._hit("request")

    async def on_response(self, ctx: CallContext) -> None:
        self._hit("response")

    async def on_complete(self, ctx: CallContext) -> None:
        self._hit("complete")

    async def startup(self) -> None:
        self.log.append(f"{self.name}.startup")

    async def shutdown(self) -> None:
        self._hit("shutdown")


def make_ctx() -> CallContext:
    return CallContext(agent_id="a", public_name="s__t", server="s", tool="t", arguments={"x": 1})


def ok_result(text: str = "ok") -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)])


async def ok_downstream(ctx: CallContext) -> CallToolResult:
    return ok_result()


async def test_independent_request_and_response_orders() -> None:
    log: list[str] = []
    a, b = Recorder("a", log), Recorder("b", log)
    pipe = Pipeline(request=[a, b], response=[a, b])  # same order both ways: impossible in an onion
    ctx = make_ctx()
    result = await pipe.run(ctx, ok_downstream)
    assert not result.is_error
    assert ctx.status == "allowed"
    assert log == ["a.request", "b.request", "a.response", "b.response", "a.complete", "b.complete"]
    assert ctx.latency_total_ms is not None
    assert ctx.latency_downstream_ms is not None


async def test_denial_stops_call_but_completion_runs() -> None:
    log: list[str] = []
    called = False

    async def downstream(ctx: CallContext) -> CallToolResult:
        nonlocal called
        called = True
        return ok_result()

    pipe = Pipeline(request=[Recorder("auth", log, fail_on="request"), Recorder("audit", log)], response=[])
    ctx = make_ctx()
    result = await pipe.run(ctx, downstream)
    assert result.is_error
    assert not called
    assert ctx.status == "denied"
    assert ctx.reason == "test: nope"
    assert isinstance(result.content[0], TextContent)
    assert "nope" in result.content[0].text
    assert log == ["auth.request", "auth.complete", "audit.complete"]


async def test_response_block_replaces_result() -> None:
    log: list[str] = []
    blocker = Recorder("scan", log, fail_on="response", exc=PipelineDenied("injection", status="blocked"))
    ctx = make_ctx()
    result = await Pipeline(request=[], response=[blocker]).run(ctx, ok_downstream)
    assert result.is_error
    assert ctx.status == "blocked"


async def test_downstream_exception_fails_closed() -> None:
    async def boom(ctx: CallContext) -> CallToolResult:
        raise ConnectionError("down")

    log: list[str] = []
    ctx = make_ctx()
    result = await Pipeline(request=[Recorder("a", log)], response=[Recorder("r", log)]).run(ctx, boom)
    assert result.is_error
    assert ctx.status == "error"
    assert ctx.reason == "ConnectionError: down"
    assert "r.response" not in log
    assert log[-2:] == ["a.complete", "r.complete"]


async def test_unknown_tool() -> None:
    async def unknown(ctx: CallContext) -> CallToolResult:
        raise UnknownToolError(ctx.public_name)

    ctx = make_ctx()
    result = await Pipeline([], []).run(ctx, unknown)
    assert result.is_error
    assert ctx.status == "error"
    assert ctx.reason is not None
    assert ctx.reason.startswith("unknown_tool")


async def test_downstream_tool_error_is_passed_through() -> None:
    async def tool_error(ctx: CallContext) -> CallToolResult:
        return CallToolResult(content=[TextContent(type="text", text="Unknown portfolio")], is_error=True)

    ctx = make_ctx()
    result = await Pipeline([], []).run(ctx, tool_error)
    assert result.is_error
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "Unknown portfolio"
    assert ctx.status == "error"


async def test_failing_on_complete_does_not_stop_others() -> None:
    log: list[str] = []
    bad = Recorder("bad", log, fail_on="complete", exc=RuntimeError("db down"))
    ctx = make_ctx()
    result = await Pipeline(request=[bad, Recorder("ok", log)], response=[]).run(ctx, ok_downstream)
    assert not result.is_error
    assert log[-2:] == ["bad.complete", "ok.complete"]


async def test_shared_middleware_runs_lifecycle_once() -> None:
    log: list[str] = []
    a = Recorder("a", log, fail_on="shutdown", exc=RuntimeError("x"))
    pipe = Pipeline(request=[a], response=[a])
    await pipe.startup()
    await pipe.run(make_ctx(), ok_downstream)
    await pipe.shutdown()  # errors are logged, not raised
    assert log.count("a.startup") == 1
    assert log.count("a.complete") == 1
    assert log.count("a.shutdown") == 1


async def test_list_tools_hook() -> None:
    class HideB(Middleware):
        async def on_list_tools(self, ctx: ListContext, tools: list[Tool]) -> list[Tool]:
            return [t for t in tools if t.name != "b"]

    tools = [Tool(name=n, input_schema={"type": "object"}) for n in ("a", "b")]
    out = await Pipeline(request=[HideB()], response=[]).list_tools(ListContext(agent_id="x"), tools)
    assert [t.name for t in out] == ["a"]


def test_registry_unknown_middleware() -> None:
    cfg = GatewayConfig.model_validate(
        {"servers": {"s": {"transport": "stdio", "command": "x"}}, "pipeline": {"request": ["nope"]}}
    )
    with pytest.raises(ValueError, match="unknown middleware"):
        build_pipeline(cfg, Services(cfg))


def test_registry_audit_needs_database_url() -> None:
    cfg = GatewayConfig.model_validate(
        {"servers": {"s": {"transport": "stdio", "command": "x"}}, "pipeline": {"request": ["audit"]}}
    )
    with pytest.raises(ValueError, match="DATABASE_URL"):
        build_pipeline(cfg, Services(cfg))


def test_registry_shares_instances() -> None:
    made: list[str] = []

    def factory(cfg: GatewayConfig, services: Services) -> Middleware:
        made.append("x")
        return Middleware()

    cfg = GatewayConfig.model_validate(
        {
            "servers": {"s": {"transport": "stdio", "command": "x"}},
            "pipeline": {"request": ["m"], "response": ["m"]},
        }
    )
    pipe = build_pipeline(cfg, Services(cfg), {"m": factory})
    assert made == ["x"]
    assert pipe.request[0] is pipe.response[0]
