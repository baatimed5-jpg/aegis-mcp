# AegisMCP: MCP gateway (PFE project)

**At session start, read docs/PROGRESS.md and docs/HANDOFF.md, then ONLY the section of
docs/PROJECT_SPEC.md for the current phase. Do not re-read the whole repo.**

## Summary
A gateway between LLM agents and MCP servers. To agents it is an MCP server; to tools it is an MCP client.
It aggregates servers as `<server>__<tool>` and runs a config-ordered middleware pipeline:
request: auth → policy/approval → injection scan → audit;
response: PII mask → injection scan → optimizer → metrics.
Contributions: (A) ML prompt-injection detector (rules / DeBERTa / LLM / combined);
(B) learned context optimizer (field pruning, compact format, cache, evidence recovery).
Architecture: docs/ARCHITECTURE.md · Decisions: docs/DECISIONS.md · Full spec: docs/PROJECT_SPEC.md

## Tech stack
Python 3.12 + uv · `mcp` SDK (Streamable HTTP + stdio) · FastAPI, Pydantic v2 · OpenAI Agents SDK
+ LiteLLM (Gemini Flash default) · PyTorch, transformers, datasets, scikit-learn, LightGBM,
sentence-transformers · Presidio · PostgreSQL 16 (SQLAlchemy 2, Alembic) · Redis 7 · pandas,
matplotlib · Angular · pytest, ruff, mypy (strict on gateway/), pre-commit · Docker Compose, GitHub Actions.

## Folders
config/ (gateway.yaml, policies, pruning rules, eval profiles) · servers/wealth, servers/malicious ·
gateway/ (pipeline/, middlewares/, detectors/, db/, api/) · ml/ (injection/, pruning/) ·
agents/ (assistant, redteam) · evaluation/ · dashboard/ · tests/ (unit/, integration/) · docs/

## Ports
gateway 8000 · wealth 8001 · malicious 8002 · dashboard 4200 · postgres 5432 · redis 6379

## Commands (available from Phase 1)
- Install: `uv sync --all-groups`
- Infra: `docker compose up -d postgres redis`
- Test: `uv run pytest` (unit only: `uv run pytest tests/unit`)
- Lint/types: `uv run ruff check . && uv run ruff format --check . && uv run mypy`
- Wealth server: `uv run python -m servers.wealth` (:8001)
- Gateway: `uv run python -m gateway` (HTTP :8000) · `uv run python -m gateway --stdio --agent <id>`

## Engineering rules
- Work phase by phase; at the end of a phase: full test suite, commit, update PROGRESS.md,
  rewrite HANDOFF.md (≤40 lines), print the new-session message, then STOP.
- Never fake or hard-code results; report failures honestly.
- Don't guess library APIs: check the installed source in .venv site-packages. Pin all versions.
- No new features or technologies without asking the user. Record decisions in docs/DECISIONS.md.
- Type hints and docstrings everywhere; async for I/O; fixed seeds (42).
- Unit tests per module + integration tests with real Postgres/Redis. Keep the suite green.
- Secrets only in .env (gitignored). Never log keys. Never commit datasets or weights over 50 MB.
- Token saving: grep/glob before opening files; run only the relevant tests while working.
