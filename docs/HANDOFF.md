# Handoff

**Phase just finished:** Phase 2 (pass-through gateway, pipeline, audit log), 2026-11-18, committed.
**Next phase:** Phase 3 (auth, policies, approvals, PII, rule-based injection scan, malicious server).

## Files created/changed
gateway/{config,namespacing,downstream,mcp_facade,app,__main__}.py · gateway/pipeline/{context,base,
runner,errors}.py · gateway/middlewares/{audit,registry}.py · gateway/db/{models,session}.py +
migrations/ (Alembic async, rev 8c6334be1acc: experiment_run, audit_call) · alembic.ini ·
config/gateway.yaml · tests/unit/gateway/* · tests/integration/test_gateway_e2e.py ·
deps pinned: fastapi 0.164.0, sqlalchemy 2.1.9, asyncpg 0.32.0, alembic 1.20.1, pyyaml 6.0.3 · D17–D20.

## How to run and test
- PATH: uv at %APPDATA%\Python\Python312\Scripts. `docker compose up -d postgres redis`.
- Local Postgres is on host port **5433** (a native PostgreSQL owns 5432, D17); `.env` is set up.
- `uv run alembic upgrade head` · `uv run python -m servers.wealth` · `uv run python -m gateway`
  → http://127.0.0.1:8000/mcp (+ /healthz). Stdio: `python -m gateway --stdio --agent assistant`.
- `uv run pytest` (76 pass, 91% cov; integration tests create+migrate DB `aegis_test`).

## Key facts for Phase 3
- Middleware = subclass `gateway.pipeline.base.Middleware`, register a factory in
  `middlewares/registry.py::FACTORIES`, add its name to `pipeline.request/response` in gateway.yaml.
- Deny with `raise PipelineDenied(reason, code, status="denied"|"blocked"|"timeout")`; runner turns it
  into `isError` + audit status. Metrics for audit go in `ctx.extra` (injection_score, pii_entities, ...).
- HTTP agent id is `config.default_agent` today: the facade's `resolve_agent(ctx)` gets the
  `ServerRequestContext`; `ctx.request` is the Starlette request (read `Authorization: Bearer` there).
  Stdio agent = `--agent`. `CallContext.meta` holds the caller's `_meta` (D4).
- Tests: never open the gateway in an async pytest fixture (anyio cancel-scope error); use an
  `asynccontextmanager` inside the test (see tests/unit/gateway/test_gateway_inprocess.py).

## Known issues / open points
- Downstream result `_meta` (serverInfo "wealth") is passed through to agents (D18); revisit if unwanted.
- No `tools/list_changed` refresh yet. CI not yet run on GitHub (no remote). Audit stores raw args
  (masking comes with PII in Phase 3).
- D4, benign sample count, Presidio model, Gemini model id: still open.

## First steps for Phase 3
1. Read PROJECT_SPEC §9 Phase 3, ARCHITECTURE §1.2 + §5, DECISIONS D2, D5, D7, D10.
2. Ask the user before adding Presidio/spaCy model (size) and confirm the policy YAML format.
3. Build auth → policy → approval → pii_mask → injection_scan (rules), then servers/malicious.
