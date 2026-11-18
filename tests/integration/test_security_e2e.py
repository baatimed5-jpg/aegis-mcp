"""Integration tests (Phase 3 definition of done), over real HTTP processes and a real Postgres.

The gateway runs with the full security pipeline (auth → pii_mask → policy → injection_scan → audit;
responses: pii_mask → injection_scan) in front of the wealth server and the malicious server. Tests
prove: unauthorized calls are denied, policy rules work (incl. the approval queue over REST), PII is
masked, and known injections from the malicious server are caught; everything lands in Postgres.

Needs ``docker compose up -d postgres``.
"""

from __future__ import annotations

import os
import secrets
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from servers.malicious.payloads import RESPONSE_INJECTIONS
from tests.integration.helpers import ROOT, connect, free_port, spawn, stop, wait_port

pytestmark = pytest.mark.integration

KEYS = {
    "AGENT_TOKEN_ASSISTANT": secrets.token_urlsafe(24),
    "AGENT_TOKEN_REDTEAM": secrets.token_urlsafe(24),
    "ADMIN_TOKEN": secrets.token_urlsafe(24),
}
APPROVAL_TIMEOUT_S = 4
PLACEHOLDER = "[removed by AegisMCP: possible prompt injection]"
TRANSFER = {"from_portfolio_id": "PF-00001", "to_portfolio_id": "PF-00002", "amount": 12.5}


@pytest.fixture(scope="module")
def malicious_url() -> Iterator[str]:
    port = free_port()
    proc = spawn(["-m", "servers.malicious", "--port", str(port)])
    try:
        wait_port(port, proc, "malicious server")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        stop(proc)


@pytest.fixture(scope="module")
def secure_gateway(
    tmp_path_factory: pytest.TempPathFactory, wealth_url: str, malicious_url: str, test_db_url: str
) -> Iterator[str]:
    """Gateway process with the repo's policies and security pipeline; returns its base URL."""
    path = tmp_path_factory.mktemp("sec") / "gateway.yaml"
    policies = (ROOT / "config" / "policies.yaml").as_posix()
    path.write_text(
        f"""servers:
  wealth: {{transport: streamable_http, url: '{wealth_url}'}}
  malicious: {{transport: streamable_http, url: '{malicious_url}'}}
agents:
  assistant: {{token_env: AGENT_TOKEN_ASSISTANT}}
  redteam: {{token_env: AGENT_TOKEN_REDTEAM}}
policies_file: '{policies}'
approval_timeout_s: {APPROVAL_TIMEOUT_S}
admin_token_env: ADMIN_TOKEN
pipeline:
  request: [auth, pii_mask, policy, injection_scan, audit]
  response: [pii_mask, injection_scan]
middlewares:
  injection_scan: {{threshold: 0.5, on_description: hide, on_argument: block, on_response: sanitize}}
""",
        encoding="utf-8",
    )
    port = free_port()
    env = {**os.environ, "DATABASE_URL": test_db_url, **KEYS}
    proc = spawn(["-m", "gateway", "--config", str(path), "--port", str(port)], env)
    try:
        wait_port(port, proc, "gateway")
        yield f"http://127.0.0.1:{port}"
    finally:
        stop(proc)


@asynccontextmanager
async def agent(base: str, key: str | None) -> AsyncIterator[Client]:
    """MCP client that sends ``Authorization: Bearer <key>`` (or no header)."""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    async with (
        httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(30, read=120)) as http,
        Client(streamable_http_client(f"{base}/mcp", http_client=http)) as client,
    ):
        yield client


async def fetch(db_url: str, query: str, *args: Any) -> list[dict[str, Any]]:
    conn = await connect(db_url)
    try:
        return [dict(r) for r in await conn.fetch(query, *args)]
    finally:
        await conn.close()


async def last_audit(db_url: str, agent_id: str, tool: str) -> dict[str, Any]:
    rows = await fetch(
        db_url,
        "SELECT * FROM audit_call WHERE agent_id=$1 AND tool=$2 ORDER BY ts DESC LIMIT 1",
        agent_id,
        tool,
    )
    assert rows, f"no audit row for {agent_id}/{tool}"
    return rows[0]


# ---- 1. unauthorized calls are denied ------------------------------------------------------------
@pytest.mark.parametrize("key", [None, "not-a-real-key"])
async def test_unauthorized_is_denied(secure_gateway: str, test_db_url: str, key: str | None) -> None:
    marker = f"unauth-{uuid.uuid4().hex[:8]}"
    async with agent(secure_gateway, key) as client:
        tools = (await client.list_tools()).tools
        res = await client.call_tool("wealth__get_client", {"name": marker})
    assert tools == []
    assert res.is_error
    rows = await fetch(test_db_url, "SELECT * FROM audit_call WHERE arguments::text LIKE $1", f"%{marker}%")
    assert len(rows) == 1
    assert (rows[0]["status"], rows[0]["agent_id"]) == ("denied", "unauthenticated")
    assert rows[0]["reason"].startswith("unauthorized")


# ---- 2. policy rules work ------------------------------------------------------------------------
async def test_policy_rules(secure_gateway: str, test_db_url: str) -> None:
    async with agent(secure_gateway, KEYS["AGENT_TOKEN_REDTEAM"]) as red:
        red_tools = {t.name for t in (await red.list_tools()).tools}
        denied = await red.call_tool("wealth__transfer_funds", TRANSFER)
        allowed = await red.call_tool("wealth__get_holdings", {"portfolio_id": "PF-00001"})
    assert "wealth__transfer_funds" not in red_tools
    assert {"wealth__get_client", "wealth__get_holdings", "malicious__get_market_news"} <= red_tools
    assert denied.is_error
    assert not allowed.is_error
    row = await last_audit(test_db_url, "redteam", "transfer_funds")
    assert (row["status"], row["reason"].split(":")[0]) == ("denied", "policy")


async def _approve_when_pending(base: str, decision: str) -> dict[str, Any]:
    admin = {"Authorization": f"Bearer {KEYS['ADMIN_TOKEN']}"}
    async with httpx.AsyncClient(base_url=base, headers=admin) as http:
        with anyio.fail_after(10):
            while True:
                pending = (await http.get("/api/approvals")).json()
                if pending:
                    break
                await anyio.sleep(0.1)
        res = await http.post(
            f"/api/approvals/{pending[0]['id']}", json={"decision": decision, "decided_by": "it"}
        )
        res.raise_for_status()
        return dict(res.json())


@pytest.mark.parametrize(("decision", "status"), [("approve", "approved"), ("deny", "denied")])
async def test_approval_over_rest(secure_gateway: str, test_db_url: str, decision: str, status: str) -> None:
    reference = f"appr-{uuid.uuid4().hex[:8]}"
    results: list[Any] = []
    decided: list[dict[str, Any]] = []
    async with agent(secure_gateway, KEYS["AGENT_TOKEN_ASSISTANT"]) as client:

        async def call() -> None:
            results.append(
                await client.call_tool("wealth__transfer_funds", {**TRANSFER, "reference": reference})
            )

        async def decide() -> None:
            decided.append(await _approve_when_pending(secure_gateway, decision))

        async with anyio.create_task_group() as tg:
            tg.start_soon(call)
            tg.start_soon(decide)
    assert results[0].is_error is (decision == "deny")
    audit = (
        await fetch(test_db_url, "SELECT * FROM audit_call WHERE arguments->>'reference' = $1", reference)
    )[0]
    assert audit["status"] == status
    approval = (
        await fetch(test_db_url, "SELECT * FROM approval WHERE id = $1", uuid.UUID(decided[0]["id"]))
    )[0]
    assert (approval["status"], approval["decided_by"], approval["audit_call_id"]) == (
        status,
        "it",
        audit["id"],
    )


async def test_approval_timeout(secure_gateway: str, test_db_url: str) -> None:
    reference = f"tmo-{uuid.uuid4().hex[:8]}"
    async with agent(secure_gateway, KEYS["AGENT_TOKEN_ASSISTANT"]) as client:
        res = await client.call_tool("wealth__transfer_funds", {**TRANSFER, "reference": reference})
    assert res.is_error
    audit = (
        await fetch(test_db_url, "SELECT * FROM audit_call WHERE arguments->>'reference' = $1", reference)
    )[0]
    assert audit["status"] == "timeout"
    approvals = await fetch(test_db_url, "SELECT status FROM approval WHERE audit_call_id = $1", audit["id"])
    assert approvals == [{"status": "timeout"}]


async def test_admin_api_requires_key(secure_gateway: str) -> None:
    async with httpx.AsyncClient(base_url=secure_gateway) as http:
        assert (await http.get("/api/approvals")).status_code == 401
        bad = {"Authorization": f"Bearer {KEYS['AGENT_TOKEN_ASSISTANT']}"}  # an agent key is not an admin key
        assert (await http.get("/api/approvals", headers=bad)).status_code == 401


# ---- 3. PII is masked ----------------------------------------------------------------------------
async def test_pii_is_masked(secure_gateway: str, wealth_url: str, test_db_url: str) -> None:
    async with Client(wealth_url) as direct:
        raw = (await direct.call_tool("get_client", {"client_id": "CL-0002"})).structured_content
    marker = f"pii-{uuid.uuid4().hex[:6]}@example.com"
    async with agent(secure_gateway, KEYS["AGENT_TOKEN_ASSISTANT"]) as client:
        res = await client.call_tool("wealth__get_client", {"client_id": "CL-0002"})
        await client.call_tool("wealth__get_client", {"name": marker})
    assert raw is not None
    assert res.structured_content is not None
    masked = res.structured_content
    assert (masked["email"], masked["iban"], masked["tax_id"]) == (
        "<EMAIL_ADDRESS>",
        "<IBAN_CODE>",
        "<US_SSN>",
    )
    assert masked["client_id"] == raw["client_id"]
    assert masked["net_worth"] == raw["net_worth"]
    text = res.content[0].text  # type: ignore[union-attr]
    assert raw["email"] not in text
    assert raw["iban"] not in text
    row = await last_audit(test_db_url, "assistant", "get_client")
    assert row["arguments"] == '{"name": "<EMAIL_ADDRESS>"}'  # audit never stores the raw email
    leaked = await fetch(test_db_url, "SELECT 1 FROM audit_call WHERE arguments::text LIKE $1", f"%{marker}%")
    assert leaked == []


# ---- 4. known injections from the malicious server are caught -------------------------------------
async def test_malicious_server_injections(secure_gateway: str, test_db_url: str) -> None:
    async with agent(secure_gateway, KEYS["AGENT_TOKEN_ASSISTANT"]) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        poisoned = await client.call_tool("malicious__get_exchange_rate", {"base": "EUR", "quote": "USD"})
        pages = [
            await client.call_tool("malicious__get_market_news", {"ticker": "acme", "page": p})
            for p in range(len(RESPONSE_INJECTIONS))
        ]
        benign = await client.call_tool("malicious__get_fund_factsheet", {"fund_id": "F-1"})
    assert "malicious__get_exchange_rate" not in names
    assert "malicious__get_analyst_rating" not in names
    assert "malicious__get_market_news" in names
    assert poisoned.is_error
    for page, res in enumerate(pages):
        assert not res.is_error
        assert res.structured_content is not None
        assert res.structured_content["articles"][1]["body"] == PLACEHOLDER, page
        assert RESPONSE_INJECTIONS[page] not in res.content[0].text  # type: ignore[union-attr]
    assert not benign.is_error
    news_calls = await fetch(
        test_db_url,
        "SELECT a.id, a.status, a.injection_score, count(e.id) AS events FROM audit_call a "
        "JOIN security_event e ON e.audit_call_id = a.id "
        "WHERE a.agent_id='assistant' AND a.tool='get_market_news' AND e.action='sanitize' "
        "GROUP BY a.id ORDER BY a.ts DESC LIMIT $1",
        len(RESPONSE_INJECTIONS),
    )
    assert len(news_calls) == len(RESPONSE_INJECTIONS)
    assert all(
        r["status"] == "allowed" and r["injection_score"] >= 0.5 and r["events"] == 1 for r in news_calls
    )
    descriptions = await fetch(
        test_db_url,
        "SELECT DISTINCT tool FROM security_event WHERE kind='description' AND server='malicious'",
    )
    assert {r["tool"] for r in descriptions} == {"get_exchange_rate", "get_analyst_rating"}
    blocked = await last_audit(test_db_url, "assistant", "get_exchange_rate")
    assert (blocked["status"], blocked["reason"].split(":")[0]) == ("blocked", "tool_flagged")
