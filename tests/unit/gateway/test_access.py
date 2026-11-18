"""Unit tests: API-key auth, policies and the approval queue (Phase 3 DONE criteria 1 and 2)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import anyio
import httpx
import pytest
from fastapi import FastAPI
from mcp import Client

from gateway.api.approvals import approvals_router
from gateway.app import Gateway
from gateway.approvals import ApprovalNotFound, ApprovalService
from gateway.config import DEFAULT_CONFIG_PATH, GatewayConfig, load_config
from gateway.mcp_facade import Identity, bearer_identity, build_mcp_server
from gateway.middlewares.access import UNAUTHENTICATED, AuthMiddleware, PolicyMiddleware
from gateway.middlewares.audit import AuditMiddleware
from gateway.pipeline.runner import Pipeline
from gateway.policy import PolicySet, load_policies
from gateway.services import Services
from gateway.store import MemoryStore
from servers.wealth.server import build_server

KEYS = {"assistant": "key-assistant-123", "redteam": "key-redteam-456"}
POLICIES = PolicySet.model_validate(
    {
        "agents": {
            "assistant": {"allow": ["wealth__*"], "require_approval": ["wealth__transfer_funds"]},
            "redteam": {"allow": ["wealth__get_*", "wealth__list_*"]},
        }
    }
)
CONFIG = GatewayConfig.model_validate(
    {"servers": {"wealth": {"transport": "streamable_http", "url": "http://unused/mcp"}}}
)
TRANSFER = {"from_portfolio_id": "PF-00001", "to_portfolio_id": "PF-00002", "amount": 5000.0}


@asynccontextmanager
async def secured(store: MemoryStore, timeout_s: float = 5.0) -> AsyncIterator[Gateway]:
    """In-process gateway with auth → policy → audit over the wealth server."""
    approvals = ApprovalService(store, timeout_s=timeout_s)
    services = Services(CONFIG, store=store)
    services._approvals = approvals
    pipeline = Pipeline(
        request=[AuthMiddleware(KEYS), PolicyMiddleware(POLICIES, approvals), AuditMiddleware(store)],
        response=[],
    )
    async with Gateway(
        CONFIG, targets={"wealth": build_server()}, pipeline=pipeline, services=services
    ) as gw:
        yield gw


# ---- policies ------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("agent", "tool", "expected"),
    [
        ("assistant", "wealth__get_holdings", "allow"),
        ("assistant", "wealth__transfer_funds", "approval"),
        ("assistant", "malicious__get_market_news", "deny"),
        ("redteam", "wealth__get_client", "allow"),
        ("redteam", "wealth__list_portfolios", "allow"),
        ("redteam", "wealth__transfer_funds", "deny"),
        ("stranger", "wealth__get_client", "deny"),
        ("assistant", "WEALTH__get_holdings", "deny"),  # matching is case-sensitive
    ],
)
def test_policy_decisions(agent: str, tool: str, expected: str) -> None:
    assert POLICIES.decide(agent, tool) == expected


def test_repo_policy_file() -> None:
    config = load_config(DEFAULT_CONFIG_PATH)
    assert config.policies_file is not None
    policies = load_policies(config.policies_file)
    assert policies.decide("assistant", "wealth__transfer_funds") == "approval"
    assert policies.decide("redteam", "wealth__transfer_funds") == "deny"
    assert policies.decide("anonymous", "wealth__get_client") == "deny"


def test_policy_file_rejects_unknown_keys(tmp_path: Path) -> None:
    bad = tmp_path / "p.yaml"
    bad.write_text("agents: {a: {allow: ['x'], deny: ['y']}}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="deny"):
        load_policies(bad)


# ---- authentication ------------------------------------------------------------------------------
def test_auth_identify() -> None:
    auth = AuthMiddleware({**KEYS, "nokey": ""})
    assert auth.identify("key-assistant-123") == "assistant"
    assert auth.identify("key-redteam-456") == "redteam"
    assert auth.identify("key-assistant-12") is None
    assert auth.identify("") is None
    assert auth.identify(None) is None


async def test_unauthorized_calls_are_denied_and_audited() -> None:
    store = MemoryStore()
    async with secured(store) as gw:
        missing = await gw.call_tool("anonymous", "wealth__get_client", {"client_id": "CL-0001"},
                                     authenticated=False)  # fmt: skip
        wrong = await gw.call_tool("anonymous", "wealth__get_client", {"client_id": "CL-0001"},
                                   credential="guess", authenticated=False)  # fmt: skip
        ok = await gw.call_tool("anonymous", "wealth__get_client", {"client_id": "CL-0001"},
                                credential=KEYS["redteam"], authenticated=False)  # fmt: skip
        assert await gw.list_tools("anonymous", credential="guess", authenticated=False) == []
    assert missing.is_error
    assert wrong.is_error
    assert not ok.is_error
    rows = list(store.audit.values())
    assert [r["status"] for r in rows] == ["denied", "denied", "allowed"]
    assert [r["agent_id"] for r in rows] == [UNAUTHENTICATED, UNAUTHENTICATED, "redteam"]
    assert rows[0]["reason"] == "unauthorized: missing API key"
    assert rows[1]["reason"] == "unauthorized: invalid API key"


async def test_policy_denies_and_filters_tools() -> None:
    store = MemoryStore()
    async with secured(store) as gw:
        res = await gw.call_tool("redteam", "wealth__transfer_funds", TRANSFER)
        red_tools = {t.name for t in await gw.list_tools("redteam")}
        assistant_tools = {t.name for t in await gw.list_tools("assistant")}
    assert res.is_error
    assert "may not call" in res.content[0].text  # type: ignore[union-attr]
    row = next(iter(store.audit.values()))
    assert (row["status"], row["reason"].split(":")[0]) == ("denied", "policy")
    assert "wealth__transfer_funds" not in red_tools
    assert "wealth__get_holdings" in red_tools
    assert "wealth__transfer_funds" in assistant_tools  # visible: callable with approval


async def test_bearer_header_over_mcp() -> None:
    """The HTTP resolver reads ``Authorization: Bearer``; the auth middleware checks it."""
    store = MemoryStore()

    def fake_ctx(header: str | None) -> Any:
        headers = {"authorization": header} if header is not None else {}
        return SimpleNamespace(request=SimpleNamespace(headers=headers))

    resolver = bearer_identity("anonymous")
    assert resolver(fake_ctx("Bearer abc")) == Identity("anonymous", "abc", False)
    assert resolver(fake_ctx("Basic abc")) == Identity("anonymous", None, False)
    assert resolver(fake_ctx(None)) == Identity("anonymous", None, False)
    async with secured(store) as gw:
        agent_server = build_mcp_server(gw, lambda _c: Identity("anonymous", KEYS["assistant"], False))
        async with Client(agent_server) as agent:
            tools = (await agent.list_tools()).tools
            res = await agent.call_tool("wealth__get_holdings", {"portfolio_id": "PF-00001"})
    assert len(tools) == 6
    assert not res.is_error
    assert next(iter(store.audit.values()))["agent_id"] == "assistant"


# ---- approvals -----------------------------------------------------------------------------------
async def _wait_pending(service: ApprovalService) -> uuid.UUID:
    with anyio.fail_after(5):
        while not service.pending():  # noqa: ASYNC110 - polling a queue owned by the code under test
            await anyio.sleep(0.01)
    return service.pending()[0].id


@pytest.mark.parametrize(("approve", "status"), [(True, "approved"), (False, "denied")])
async def test_approval_decisions(approve: bool, status: str) -> None:
    store = MemoryStore()
    results: list[Any] = []
    async with secured(store) as gw:
        approvals = gw.services.approvals

        async def call() -> None:
            results.append(await gw.call_tool("assistant", "wealth__transfer_funds", TRANSFER))

        async with anyio.create_task_group() as tg:
            tg.start_soon(call)
            approval_id = await _wait_pending(approvals)
            pending = approvals.pending()[0].public()
            assert pending["tool"] == "wealth__transfer_funds"
            assert pending["arguments"] == TRANSFER
            await approvals.decide(approval_id, approve=approve, decided_by="tester")
    assert results[0].is_error is (not approve)
    audit = next(iter(store.audit.values()))
    assert audit["status"] == status
    approval = store.approvals[approval_id]
    assert (approval["status"], approval["decided_by"]) == (status, "tester")
    assert approval["audit_call_id"] == audit["id"]
    assert approvals.pending() == []


async def test_approval_timeout() -> None:
    store = MemoryStore()
    async with secured(store, timeout_s=0.2) as gw:
        res = await gw.call_tool("assistant", "wealth__transfer_funds", TRANSFER)
        with pytest.raises(ApprovalNotFound):
            await gw.services.approvals.decide(uuid.uuid4(), approve=True, decided_by="late")
    assert res.is_error
    audit = next(iter(store.audit.values()))
    assert (audit["status"], audit["reason"].split(":")[0]) == ("timeout", "approval_timeout")
    assert next(iter(store.approvals.values()))["status"] == "timeout"


async def test_approval_without_queue_is_denied() -> None:
    mw = PolicyMiddleware(POLICIES, approvals=None)
    store = MemoryStore()
    pipeline = Pipeline(request=[mw, AuditMiddleware(store)], response=[])
    async with Gateway(CONFIG, targets={"wealth": build_server()}, pipeline=pipeline) as gw:
        res = await gw.call_tool("assistant", "wealth__transfer_funds", TRANSFER)
    assert res.is_error
    assert next(iter(store.audit.values()))["reason"].startswith("approval_unavailable")


async def test_approvals_rest_api(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryStore()
    results: list[Any] = []
    async with secured(store) as gw:
        app = FastAPI()
        app.include_router(approvals_router(gw))
        transport = httpx.ASGITransport(app=app)
        admin = {"Authorization": "Bearer admin-secret"}
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
            monkeypatch.delenv("ADMIN_TOKEN", raising=False)
            assert (await http.get("/api/approvals", headers=admin)).status_code == 503
            monkeypatch.setenv("ADMIN_TOKEN", "admin-secret")
            assert (await http.get("/api/approvals")).status_code == 401
            assert (
                await http.get("/api/approvals", headers={"Authorization": "Bearer x"})
            ).status_code == 401

            async def call() -> None:
                results.append(await gw.call_tool("assistant", "wealth__transfer_funds", TRANSFER))

            async with anyio.create_task_group() as tg:
                tg.start_soon(call)
                approval_id = await _wait_pending(gw.services.approvals)
                listed = (await http.get("/api/approvals", headers=admin)).json()
                assert [item["id"] for item in listed] == [str(approval_id)]
                bad = await http.post(
                    f"/api/approvals/{approval_id}", headers=admin, json={"decision": "maybe"}
                )
                assert bad.status_code == 422
                ok = await http.post(
                    f"/api/approvals/{approval_id}",
                    headers=admin,
                    json={"decision": "approve", "decided_by": "ana"},
                )
                assert ok.status_code == 200
                assert ok.json()["status"] == "approved"
            again = await http.post(f"/api/approvals/{approval_id}", headers=admin, json={"decision": "deny"})
            assert again.status_code == 404
    assert not results[0].is_error
    assert next(iter(store.audit.values()))["status"] == "approved"


def test_services_without_database() -> None:
    services = Services(CONFIG)
    assert not services.has_store
    with pytest.raises(ValueError, match="DATABASE_URL"):
        _ = services.store
    assert cast(Any, services)._store is None


def test_registry_builds_repo_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The repo's gateway.yaml builds every Phase 3 middleware in the configured order."""
    from gateway.middlewares.registry import build_pipeline

    monkeypatch.setenv("AGENT_TOKEN_ASSISTANT", "k1")
    monkeypatch.delenv("AGENT_TOKEN_REDTEAM", raising=False)  # missing key: agent skipped, not an error
    config = load_config(DEFAULT_CONFIG_PATH)
    services = Services(config, store=MemoryStore())
    pipeline = build_pipeline(config, services)
    assert [m.name for m in pipeline.request] == ["auth", "pii_mask", "policy", "injection_scan", "audit"]
    assert [m.name for m in pipeline.response] == ["pii_mask", "injection_scan"]
    assert pipeline.request[1] is pipeline.response[0]
    auth = cast(AuthMiddleware, pipeline.request[0])
    assert auth.identify("k1") == "assistant"
    policy = cast(PolicyMiddleware, pipeline.request[2])
    assert policy.approvals is services.approvals
