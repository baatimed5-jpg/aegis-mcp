"""Unit tests: dashboard REST API, live audit hub and WebSocket auth (Phase 9 backend)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gateway.api.dashboard import dashboard_router
from gateway.app import Gateway
from gateway.config import GatewayConfig
from gateway.hub import AuditHub, audit_view
from gateway.middlewares.audit import AuditMiddleware
from gateway.pipeline.runner import Pipeline
from gateway.services import Services
from gateway.store import MemoryStore
from servers.wealth.server import build_server

ROOT = Path(__file__).resolve().parents[3]
CONFIG = GatewayConfig.model_validate(
    {
        "servers": {"wealth": {"transport": "streamable_http", "url": "http://unused/mcp"}},
        "policies_file": str(ROOT / "config" / "policies.yaml"),
        "pipeline": {"request": ["audit"], "response": []},
    }
)
ADMIN = {"Authorization": "Bearer admin-secret"}


def test_hub_fans_out_and_drops_when_full() -> None:
    hub = AuditHub()
    q = hub.subscribe()
    hub.publish({"id": "1", "status": "allowed", "secret_field": "x"})
    view = q.get_nowait()
    assert view["id"] == "1"
    assert "secret_field" not in view
    hub.unsubscribe(q)
    hub.publish({"id": "2"})
    assert q.empty()
    assert audit_view({"tokens_before": None})["tokens_before"] is None


async def test_dashboard_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "admin-secret")
    store = MemoryStore()
    services = Services(CONFIG, store=store)
    pipeline = Pipeline(request=[AuditMiddleware(store, services.hub)], response=[])
    async with Gateway(
        CONFIG, targets={"wealth": build_server()}, pipeline=pipeline, services=services
    ) as gw:
        queue = services.hub.subscribe()
        await gw.call_tool("assistant", "wealth__get_client", {"client_id": "CL-0001"})
        assert queue.get_nowait()["tool"] == "get_client"  # live push
        app = FastAPI()
        app.include_router(dashboard_router(gw))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
            assert (await http.get("/api/stats")).status_code == 401
            stats = (await http.get("/api/stats", headers=ADMIN)).json()
            assert stats["calls_total"] == 1
            assert stats["by_status"] == {"allowed": 1}
            audit = (await http.get("/api/audit?limit=5", headers=ADMIN)).json()
            assert audit[0]["tool"] == "get_client"
            assert (await http.get("/api/security-events", headers=ADMIN)).json() == []
            policies = (await http.get("/api/policies", headers=ADMIN)).json()
            assert "assistant" in policies["agents"]
            pipe = (await http.get("/api/pipeline", headers=ADMIN)).json()
            assert pipe["request"] == ["audit"]
            assert "tags" in (await http.get("/api/experiments", headers=ADMIN)).json()
            bad = await http.get("/api/experiments/files/x/..%2F..%2F.env", headers=ADMIN)
            assert bad.status_code == 404


def test_websocket_rejects_bad_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "admin-secret")
    gw = Gateway(CONFIG, targets={}, services=Services(CONFIG, store=MemoryStore()))
    app = FastAPI()
    app.include_router(dashboard_router(gw))
    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/api/ws/audit?token=wrong") as ws,
    ):
        ws.receive_json()
    assert exc.value.code == 4401
