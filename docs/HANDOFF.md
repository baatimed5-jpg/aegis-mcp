# Handoff

**Phase just finished:** Phase 0 (plan): approved by the user 2026-11-18, committed.
**Next phase:** Phase 1 (repo setup + wealth MCP server).

## Files created
CLAUDE.md, docs/PROJECT_SPEC.md, docs/ARCHITECTURE.md, docs/DECISIONS.md, docs/PROGRESS.md,
docs/HANDOFF.md. No application code yet.

## Environment (checked 2026-11-18)
Windows 11 · Python 3.12.10 · Git 2.53 · Docker 29.8 (Docker Desktop, WSL2) · Node 24.17 / npm 11.13.
**uv is NOT installed yet**; install it at user level in Phase 1 (no admin rights).
Repo path: C:\Users\asus\dev\aegis-mcp

## Open decisions
- D4: passing the user question via MCP `_meta` must be verified with the Agents SDK (Phase 4/7).
- Benign sample count for the FP rate: proposed 200 (confirm in Phase 4/8).
- Presidio spaCy model (sm vs lg): decide in Phase 3 based on size and accuracy.
- Exact Gemini Flash model id and prices: read from provider docs in Phase 4.

## First steps for Phase 1
1. Install uv (official standalone installer, user-level), then `uv init` and pin Python 3.12.
2. Check the latest `mcp` SDK version on PyPI, install it pinned, and read its server source
   (FastMCP / streamable HTTP) in .venv before writing the wealth server.
3. Set up tooling: ruff, mypy, pytest, pre-commit, CI workflow, docker-compose (postgres:16, redis:7), .env.example.
4. Build the wealth data generator (seed 42) and the 6 tools with unit tests; check them with the MCP Inspector.
