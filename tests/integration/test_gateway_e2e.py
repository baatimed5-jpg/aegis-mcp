"""Integration tests (Phase 2 definition of done).

An agent calls the wealth tools through the gateway, over both transports, and gets results identical
to calling the wealth server directly; every call is in the ``audit_call`` table of a real Postgres.

Needs ``docker compose up -d postgres`` and ``DATABASE_URL`` (env or .env). A separate database
``<name>_test`` is created and migrated with Alembic, so dev data is never touched.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from mcp import Client, StdioServerParameters
from mcp.types import CallToolResult
from sqlalchemy.engine import make_url

from gateway.config import load_env_file
from gateway.middlewares.audit import PostgresAuditStore

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]

CALLS: list[tuple[str, dict[str, Any]]] = [
    ("get_client", {"name": "a"}),  # ambiguous name -> downstream tool error, passed through
    ("get_client", {"client_id": "CL-0007"}),
    ("list_portfolios", {"client_id": "CL-0003"}),
    ("get_holdings", {"portfolio_id": "PF-00004"}),
    ("get_transactions", {"portfolio_id": "PF-00001", "start_date": "2026-01-01", "limit": 20}),
    ("get_portfolio_performance", {"portfolio_id": "PF-00005", "period": "YTD"}),
    ("transfer_funds", {"from_portfolio_id": "PF-00001", "to_portfolio_id": "PF-00002", "amount": 10.5}),
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def _wait_port(port: int, proc: subprocess.Popen[bytes], what: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"{what} exited early with code {proc.returncode}")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return
        except OSError:
            time.sleep(0.2)
    pytest.fail(f"{what} did not start within 30 s")


def _spawn(args: list[str], env: dict[str, str] | None = None) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, *args], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def _stop(proc: subprocess.Popen[bytes]) -> None:
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="module")
def test_db_url() -> str:
    """Create (if needed) and migrate a dedicated test database; return its SQLAlchemy URL."""
    load_env_file(ROOT / ".env")
    base = os.environ.get("DATABASE_URL")
    if not base:
        pytest.fail("DATABASE_URL is not set (copy .env.example to .env)")
    url = make_url(base)
    test_url = url.set(database=f"{url.database}_test")

    async def ensure_db() -> None:
        conn = await asyncpg.connect(
            user=url.username, password=url.password, host=url.host, port=url.port, database=url.database
        )
        try:
            exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", test_url.database)
            if not exists:
                await conn.execute(f'CREATE DATABASE "{test_url.database}"')
        finally:
            await conn.close()

    asyncio.run(ensure_db())
    rendered = test_url.render_as_string(hide_password=False)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["database_url"] = rendered
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    return rendered


@pytest.fixture(scope="module")
def wealth_url() -> Iterator[str]:
    port = _free_port()
    proc = _spawn(["-m", "servers.wealth", "--port", str(port)])
    try:
        _wait_port(port, proc, "wealth server")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        _stop(proc)


@pytest.fixture(scope="module")
def gateway_config(tmp_path_factory: pytest.TempPathFactory, wealth_url: str) -> Path:
    path = tmp_path_factory.mktemp("cfg") / "gateway.yaml"
    path.write_text(
        f"servers:\n  wealth: {{transport: streamable_http, url: '{wealth_url}'}}\n"
        "default_agent: http-agent\npipeline: {request: [audit], response: []}\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture(scope="module")
def gateway_env(test_db_url: str) -> dict[str, str]:
    return {**os.environ, "DATABASE_URL": test_db_url}


@pytest.fixture(scope="module")
def gateway_url(gateway_config: Path, gateway_env: dict[str, str]) -> Iterator[str]:
    port = _free_port()
    proc = _spawn(["-m", "gateway", "--config", str(gateway_config), "--port", str(port)], gateway_env)
    try:
        _wait_port(port, proc, "gateway")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        _stop(proc)


def comparable(res: CallToolResult) -> dict[str, Any]:
    return {"content": res.model_dump()["content"], "structured": res.structured_content, "err": res.is_error}


async def _connect(db_url: str) -> asyncpg.Connection:
    """Plain asyncpg connection from a SQLAlchemy URL."""
    url = make_url(db_url)
    return await asyncpg.connect(
        user=url.username, password=url.password, host=url.host, port=url.port, database=url.database
    )


async def audit_rows(db_url: str, marker: str) -> list[asyncpg.Record]:
    """Rows whose arguments carry the test's unique marker (``reference`` or ``name``)."""
    conn = await _connect(db_url)
    try:
        return list(
            await conn.fetch(
                "SELECT * FROM audit_call WHERE arguments::text LIKE $1 ORDER BY ts", f"%{marker}%"
            )
        )
    finally:
        await conn.close()


async def run_calls(agent: Client, direct: Client, marker: str) -> None:
    """Make every call through the gateway and directly; results must be identical."""
    tools = (await agent.list_tools()).tools
    assert sorted(t.name for t in tools) == sorted(
        f"wealth__{t.name}" for t in (await direct.list_tools()).tools
    )
    for tool, args in CALLS:
        tagged = {**args, "reference": marker} if tool == "transfer_funds" else args
        expected = await direct.call_tool(tool, tagged)
        got = await agent.call_tool(f"wealth__{tool}", tagged)
        assert comparable(got) == comparable(expected), tool
    # one more call that only exists to carry the marker for non-transfer tools
    res = await agent.call_tool("wealth__get_client", {"name": marker})
    assert res.is_error  # no client has that name


async def test_http_agent_through_gateway(gateway_url: str, wealth_url: str, test_db_url: str) -> None:
    marker = f"it-http-{uuid.uuid4().hex[:8]}"
    async with Client(gateway_url) as agent, Client(wealth_url) as direct:
        await run_calls(agent, direct, marker)
    rows = await audit_rows(test_db_url, marker)
    assert len(rows) == 2  # transfer_funds (reference) + get_client (name)
    by_tool = {r["tool"]: r for r in rows}
    assert by_tool["transfer_funds"]["status"] == "allowed"
    assert by_tool["get_client"]["status"] == "error"
    assert {r["agent_id"] for r in rows} == {"http-agent"}
    assert all(r["server"] == "wealth" and r["latency_total_ms"] > 0 for r in rows)


async def test_stdio_agent_through_gateway(
    gateway_config: Path, gateway_env: dict[str, str], wealth_url: str, test_db_url: str
) -> None:
    marker = f"it-stdio-{uuid.uuid4().hex[:8]}"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "gateway", "--stdio", "--agent", "assistant", "--config", str(gateway_config)],
        cwd=ROOT,
        env=gateway_env,
    )
    async with Client(params) as agent, Client(wealth_url) as direct:
        await run_calls(agent, direct, marker)
    rows = await audit_rows(test_db_url, marker)
    assert len(rows) == 2
    assert {r["agent_id"] for r in rows} == {"assistant"}


async def test_every_call_is_audited(gateway_url: str, test_db_url: str) -> None:
    """Count rows for one agent session: N calls -> N rows, none left in_progress."""
    conn = await _connect(test_db_url)
    try:
        before = await conn.fetchval("SELECT count(*) FROM audit_call")
        async with Client(gateway_url) as agent:
            for i in range(5):
                await agent.call_tool("wealth__get_holdings", {"portfolio_id": f"PF-0000{i + 1}"})
            await agent.call_tool("wealth__does_not_exist", {})
        after = await conn.fetchval("SELECT count(*) FROM audit_call")
        stuck = await conn.fetchval("SELECT count(*) FROM audit_call WHERE status = 'in_progress'")
        unknown = await conn.fetchval(
            "SELECT count(*) FROM audit_call WHERE tool = 'does_not_exist' AND reason LIKE 'unknown_tool%'"
        )
    finally:
        await conn.close()
    assert after - before == 6
    assert stuck == 0
    assert unknown >= 1


async def test_postgres_store_upsert(test_db_url: str) -> None:
    store = PostgresAuditStore.from_url(test_db_url)
    call_id = uuid.uuid4()
    row: dict[str, Any] = {
        "id": call_id, "run_id": None, "ts": datetime.now(UTC),
        "agent_id": "t", "server": "s", "tool": "t", "arguments": {"k": "v"}, "status": "in_progress",
        "reason": None, "injection_score": None, "pii_entities": None, "tokens_before": None,
        "tokens_after": None, "cost_saved_usd": None, "latency_total_ms": None,
        "latency_downstream_ms": None, "cache_hit": False,
    }  # fmt: skip
    try:
        await store.upsert(row)
        await store.upsert({**row, "status": "denied", "reason": "policy: x", "latency_total_ms": 1.5})
    finally:
        await store.close()
    conn = await _connect(test_db_url)
    try:
        got = await conn.fetch(
            "SELECT status, reason, latency_total_ms FROM audit_call WHERE id = $1", call_id
        )
    finally:
        await conn.close()
    assert [tuple(r) for r in got] == [("denied", "policy: x", 1.5)]
