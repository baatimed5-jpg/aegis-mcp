# Handoff

**Phase just finished:** Phase 3 (auth, policies, approvals, PII, rule injection scan, malicious server), 2026-11-18, committed.
**Next phase:** Phase 4 (assistant agent + benchmark tasks + baseline evaluation, no optimization).

## Files created/changed
gateway/{store,services,policy,approvals,pii}.py · gateway/middlewares/{access,pii,injection}.py (+audit, registry)
· gateway/detectors/{base,rules}.py · gateway/api/approvals.py · gateway/pipeline/content.py (JSON string walk
over structured + text copies) · migration 0ad1213972fb (approval, security_event) · servers/malicious/ ·
config/{gateway,policies}.yaml · tests: unit/gateway/test_{access,pii,injection}.py, integration/
{conftest,helpers,test_security_e2e}.py · deps: presidio-analyzer 2.2.366, en-core-web-sm 3.8.0 (wheel URL) · D21–D25.

## How to run and test
- PATH: uv at %APPDATA%\Python\Python312\Scripts. `docker compose up -d postgres redis`; `uv run alembic upgrade head`.
- `.env` now has AGENT_TOKEN_ASSISTANT, AGENT_TOKEN_REDTEAM, ADMIN_TOKEN (generated, never print them).
- HTTP agents must send `Authorization: Bearer <key>` (test helper: `streamable_http_client(url, http_client=
  httpx2.AsyncClient(headers=...))`). Stdio: `--agent <id>` is trusted. Admin: `GET/POST /api/approvals[/{id}]`.
- `uv run pytest` → 155 pass (~70 s, 90% cov). Integration tests spawn wealth+malicious+gateway processes.

## Key facts for Phase 4
- Pipeline (gateway.yaml): request [auth, pii_mask, policy, injection_scan, audit]; response [pii_mask, injection_scan].
  For the *baseline* run, decide with the user whether security stays on (it changes results: PII masked).
- `wealth__transfer_funds` needs approval for `assistant` → the call blocks up to `approval_timeout_s` (120 s).
  The agent's MCP client read timeout must exceed it, or benchmark tasks must avoid transfers / auto-approve.
- PII masks emails/IBANs/SSNs/phones in results: benchmark questions must not expect those values.
- Malicious server is `enabled: false` in gateway.yaml; enable it only for security evaluation.
- `ctx.extra` keys used: injection_score, pii_entities, pii_entities_arguments, security_events, approval_id,
  injection_sanitized. Audit columns tokens_before/after and cost_saved_usd are still empty (Phase 5).

## Known issues / open points
- Rule detector tuned on our own payloads (all 10 caught, 0 false positives on 8 benign texts and 30k wealth
  strings) → fair numbers only in Phase 6. Presidio misses ~13% of Faker phone formats (D23).
- Approvals are in-memory (single gateway instance, D7). Unauthenticated `tools/list` returns an empty list.
- No `tool_registry` table yet (description verdicts are cached in memory, events stored once per process).
- Still open: D4 (`_meta` from Agents SDK), Gemini model id, benign sample count. CI not run on GitHub (no remote).

## First steps for Phase 4
1. Read PROJECT_SPEC §9 Phase 4 + §8 (evaluation), ARCHITECTURE task list Phase 4, DECISIONS D4, D6.
2. Ask the user: Gemini model id + API key availability, security on/off for the baseline, transfer handling.
3. Verify Agents SDK + LiteLLM against the installed sources before writing the agent.
