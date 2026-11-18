# Handoff

**Phase just finished:** Phase 1 (repo setup + wealth MCP server), 2026-11-18, committed.
**Next phase:** Phase 2 (gateway pass-through proxy, pipeline, audit log).

## Files created/changed
pyproject.toml + uv.lock (exact pins: mcp 2.3.0, faker 40.54.0, pydantic 2.14.3, uvicorn 0.54.0;
dev: ruff, mypy, pytest, pytest-asyncio, pytest-cov, pre-commit) · .python-version · docker-compose.yml
· .env.example · .pre-commit-config.yaml · .github/workflows/ci.yml · servers/wealth/{models,data,server,__main__}.py
· tests/unit/servers/* · tests/integration/test_wealth_transports.py · docs/DECISIONS.md (D12–D16).

## How to run and test
uv is at %APPDATA%\Python\Python312\Scripts (added to the user PATH; Git Bash may need it exported).
- `uv sync --all-groups` · `docker compose up -d postgres redis` (start Docker Desktop first)
- `uv run python -m servers.wealth` → http://127.0.0.1:8001/mcp (`--stdio` for stdio)
- `uv run pytest` (36 pass, 96% coverage) · `uv run ruff check . && uv run mypy`
- Inspector: `npx -y @modelcontextprotocol/inspector@2.13.0` (UI) or `--cli <url> --transport http`

## Key facts for Phase 2
- mcp 2.x: `from mcp.server.mcpserver import MCPServer` (FastMCP removed); `from mcp import Client`
  takes a URL, `StdioServerParameters`, or a server instance (in-process tests). Low-level server:
  `mcp.server.lowlevel.server.Server`. Results: `CallToolResult.structured_content`, `.is_error`.
- Wealth data: 40 clients CL-0001.., 74 portfolios PF-00001.., AS_OF 2026-10-30, seed 42.
  get_holdings ≈ 20 KB JSON (good for the optimizer later).

## Known issues
- Git warns LF→CRLF on Windows (harmless). No .gitattributes yet.
- CI workflow written but not yet run on GitHub (no remote configured).

## Open decisions
- D4 (`_meta` for the user question), benign sample count (200?), Presidio model, Gemini model id: unchanged.

## First steps for Phase 2
1. Read DECISIONS D1–D3 and ARCHITECTURE §1.2, §3.1, §5 only.
2. Add pinned deps: fastapi, sqlalchemy[asyncio], asyncpg, alembic, redis, pyyaml, tiktoken.
3. Read mcp source: lowlevel `Server` (dynamic list_tools/call_tool handlers), `ClientSessionGroup`.
4. Build config loader, namespacing, downstream manager, MCP facade (HTTP + stdio), pipeline runner,
   audit middleware + DB models + Alembic; integration test: agent→gateway→wealth, row in audit table.
