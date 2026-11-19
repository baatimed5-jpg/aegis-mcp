"""Read-only admin API for the Angular dashboard + the live audit WebSocket (Phase 9).

All routes need ``Authorization: Bearer <admin key>`` (same key as ``/api/approvals``, D25); the
WebSocket takes it as ``?token=`` because browsers cannot set headers on WebSockets, and closes
with code 4401 when it is wrong.
"""

from __future__ import annotations

import asyncio
import csv
import hmac
import os
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import yaml
from fastapi import APIRouter, Depends, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from gateway.db.models import AuditCall, SecurityEvent
from gateway.hub import audit_view, jsonable
from gateway.store import MemoryStore, PostgresStore

if TYPE_CHECKING:
    from gateway.app import Gateway

ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = ROOT / "evaluation" / "results"


def _admin_key(gateway: Gateway) -> str | None:
    env = gateway.config.admin_token_env
    return os.environ.get(env) if env else None


def _valid(token: str, expected: str | None) -> bool:
    return bool(expected) and hmac.compare_digest(token.encode(), (expected or "").encode())


async def _stats(gateway: Gateway) -> dict[str, Any]:
    store = gateway.services.store
    tokens_before: Any
    tokens_after: Any
    cost: Any
    cache_hits: Any
    pii: Any
    if isinstance(store, PostgresStore):
        async with store.sessions() as s:
            by_status = dict(
                (await s.execute(select(AuditCall.status, func.count()).group_by(AuditCall.status))).all()
            )
            sums = (
                await s.execute(
                    select(
                        func.coalesce(func.sum(AuditCall.tokens_before), 0),
                        func.coalesce(func.sum(AuditCall.tokens_after), 0),
                        func.coalesce(func.sum(AuditCall.cost_saved_usd), 0),
                        func.count().filter(AuditCall.cache_hit.is_(True)),
                        func.coalesce(func.sum(AuditCall.pii_entities), 0),
                    )
                )
            ).one()
            by_kind = dict(
                (await s.execute(select(SecurityEvent.kind, func.count()).group_by(SecurityEvent.kind))).all()
            )
            blocked = (
                await s.execute(
                    select(func.count()).select_from(SecurityEvent).where(SecurityEvent.action != "flag")
                )
            ).scalar_one()
        tokens_before, tokens_after, cost, cache_hits, pii = sums
    else:
        rows = list(store.audit.values()) if isinstance(store, MemoryStore) else []
        events = store.events if isinstance(store, MemoryStore) else []
        by_status = {}
        for r in rows:
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        tokens_before = sum(r.get("tokens_before") or 0 for r in rows)
        tokens_after = sum(r.get("tokens_after") or 0 for r in rows)
        cost = sum(r.get("cost_saved_usd") or 0 for r in rows)
        cache_hits = sum(bool(r.get("cache_hit")) for r in rows)
        pii = sum(r.get("pii_entities") or 0 for r in rows)
        by_kind = {}
        for e in events:
            by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
        blocked = sum(e.get("action") != "flag" for e in events)
    return {
        "calls_total": sum(by_status.values()),
        "by_status": by_status,
        "security_events_total": sum(by_kind.values()),
        "security_events_by_kind": by_kind,
        "blocked_attacks": int(blocked),
        "tokens_before": int(tokens_before),
        "tokens_after": int(tokens_after),
        "tokens_saved": int(tokens_before) - int(tokens_after),
        "cost_saved_usd": float(cost),
        "cache_hits": int(cache_hits),
        "pii_entities": int(pii),
    }


async def _recent(
    gateway: Gateway, model: type[AuditCall] | type[SecurityEvent], limit: int
) -> list[dict[str, Any]]:
    store = gateway.services.store
    if isinstance(store, PostgresStore):
        async with store.sessions() as s:
            items = (await s.execute(select(model).order_by(model.ts.desc()).limit(limit))).scalars().all()
        return [jsonable({c.name: getattr(i, c.name) for c in model.__table__.columns}) for i in items]
    if isinstance(store, MemoryStore):
        rows = list(store.audit.values()) if model is AuditCall else list(store.events)
        return [jsonable(r) for r in sorted(rows, key=lambda r: r["ts"], reverse=True)[:limit]]
    return []


def _experiments() -> dict[str, Any]:
    tags = []
    if RESULTS_DIR.is_dir():
        for folder in sorted(p for p in RESULTS_DIR.iterdir() if p.is_dir()):
            summary: list[dict[str, Any]] = []
            path = folder / "summary.csv"
            if path.exists():
                with path.open(encoding="utf-8", newline="") as f:
                    for r in csv.DictReader(f):
                        summary.append(
                            {
                                "profile": r["profile"],
                                "metric": r["metric"],
                                "mean": float(r["mean"]),
                                "std": float(r["std"]),
                                "runs": int(r["runs"]),
                            }
                        )
            charts = [f"/api/experiments/files/{folder.name}/{p.name}" for p in sorted(folder.glob("*.png"))]
            if summary or charts:
                tags.append({"tag": folder.name, "summary": summary, "charts": charts})
    return {"tags": tags}


def dashboard_router(gateway: Gateway) -> APIRouter:
    """REST routes and the WebSocket used by the dashboard."""
    router = APIRouter(prefix="/api", tags=["dashboard"])

    def require_admin(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = _admin_key(gateway)
        if not expected:
            raise HTTPException(status_code=503, detail="admin API disabled: no admin key configured")
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not _valid(token.strip(), expected):
            raise HTTPException(status_code=401, detail="invalid admin key")

    admin = [Depends(require_admin)]

    @router.get("/stats", dependencies=admin)
    async def stats() -> dict[str, Any]:
        """Totals for the KPI cards."""
        return await _stats(gateway)

    @router.get("/audit", dependencies=admin)
    async def audit(limit: Annotated[int, Query(ge=1, le=1000)] = 100) -> list[dict[str, Any]]:
        """Most recent audited calls."""
        return [audit_view(r) for r in await _recent(gateway, AuditCall, limit)]

    @router.get("/security-events", dependencies=admin)
    async def security_events(limit: Annotated[int, Query(ge=1, le=1000)] = 100) -> list[dict[str, Any]]:
        """Most recent security events (injections hidden, blocked or sanitized)."""
        keys = ["id", "ts", "kind", "server", "tool", "detector", "score", "action", "excerpt"]
        return [{k: r.get(k) for k in keys} for r in await _recent(gateway, SecurityEvent, limit)]

    @router.get("/policies", dependencies=admin)
    async def policies() -> dict[str, Any]:
        """The policy file as JSON."""
        if not gateway.config.policies_file:
            return {"agents": {}}
        text = await asyncio.to_thread(Path(gateway.config.policies_file).read_text, encoding="utf-8")
        data = yaml.safe_load(text) or {}
        return {"agents": data.get("agents", {})}

    @router.get("/pipeline", dependencies=admin)
    async def pipeline() -> dict[str, Any]:
        """Middleware order and connected servers."""
        cfg = gateway.config
        return {
            "request": cfg.pipeline.request,
            "response": cfg.pipeline.response,
            "servers": sorted(gateway.downstream.clients),
            "detector": cfg.middlewares.get("injection_scan", {}).get("detector", "rules"),
        }

    @router.get("/experiments", dependencies=admin)
    async def experiments() -> dict[str, Any]:
        """Evaluation summaries and charts found under evaluation/results/."""
        return _experiments()

    @router.get("/experiments/files/{tag}/{name}", dependencies=admin)
    async def experiment_file(tag: str, name: str) -> FileResponse:
        """One chart image (PNG only, no path traversal)."""
        path = (RESULTS_DIR / tag / name).resolve()
        if path.suffix != ".png" or RESULTS_DIR.resolve() not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(path, media_type="image/png")

    @router.websocket("/ws/audit")
    async def ws_audit(websocket: WebSocket, token: str = "") -> None:
        """Push every finished audit row live."""
        if not _valid(token, _admin_key(gateway)):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        queue = gateway.services.hub.subscribe()
        try:
            while True:
                await websocket.send_json(await queue.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            gateway.services.hub.unsubscribe(queue)

    return router
