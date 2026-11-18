# Design Decisions

Format: **Decision**, then *Alternatives*, then *Reason*. Numbering follows ARCHITECTURE.md §2 where it applies.

## D1: Middleware interface with separate request and response orders
- **Decision**: one base class with optional hooks `on_list_tools`, `on_request`, `on_response`, and
  `on_complete`. Config holds two ordered lists, `pipeline.request` and `pipeline.response`.
- *Alternatives*: an onion/ASGI-style `__call__(ctx, next)` chain.
- *Reason*: in an onion chain the response order is always the reverse of the request order, so
  experiment (6), filter→compress vs compress→filter, could not be expressed. Two lists turn that
  experiment into a config change.

## D2: Denials are exceptions, and `on_complete` always runs
- **Decision**: `PipelineDenied(reason, code)` becomes an MCP tool error (`isError=true`). The
  completion hooks run for every call, whatever its outcome.
- *Alternatives*: return-value flags; JSON-RPC protocol errors.
- *Reason*: denied calls must still be audited. A tool-level error is readable by the agent's LLM.
  A protocol error could crash some clients.

## D3: Cache raw downstream results
- **Decision**: the cache sits at the downstream boundary, and all response middlewares run on cache hits.
  Tools with side effects are never cached.
- *Alternatives*: cache the final optimized response.
- *Reason*: pruning depends on the question, and security must apply to every response that is served.
  Caching `transfer_funds` would be a correctness bug.

## D4: User question passed in `_meta` (fallback: HTTP header)
- **Decision**: the assistant sends `aegis/question` in the request `_meta`, or `X-Aegis-Question` as a fallback.
  If the question is missing, learned pruning falls back to rule-based pruning.
- *Alternatives*: add a `question` argument to every tool (pollutes the tool schemas); infer the
  question from the arguments (weak signal).
- *Reason*: `_meta` is the protocol's own extension point. **To verify** in Phase 4/7 that the
  Agents SDK client can set it.

## D5: API keys rather than JWT
- **Decision**: one key per agent in `.env`. YAML holds only the env-var name. Comparison is
  constant-time. Over stdio, the agent id comes from a CLI flag.
- *Alternatives*: PyJWT with signed tokens.
- *Reason*: one issuer, no third-party identity, no token expiry needed in the demo. It is simpler
  and still meets the requirement ("PyJWT or API keys").

## D6: tiktoken in-path, official count API offline
- *Alternatives*: provider API on every call.
- *Reason*: a network round trip per call would distort the latency-overhead metric. The reported
  numbers still come from the official API.

## D7: Approvals use in-memory futures + a Postgres row
- *Alternatives*: Redis pub/sub; polling the database.
- *Reason*: simplest correct design for a single gateway instance. The multi-instance limitation
  is documented.

## D8: Evidence-recovery store in Redis with TTL
- *Alternatives*: a Postgres table.
- *Reason*: the data is short-lived, per call, and read by key. A TTL cleans it up automatically.

## D9: Wealth data generated in memory (Faker, seed 42)
- *Alternatives*: seed a Postgres schema for the demo server.
- *Reason*: deterministic, no extra infrastructure, and the demo server stays independent of the gateway DB.

## D10: Injection scanning per JSON string value, max over chunks
- *Alternatives*: scan the whole serialized response at once.
- *Reason*: lower latency on large responses, and the offending field can be sanitized precisely.

## D11: Single uv project with dependency groups
- *Alternatives*: a uv workspace with one package per component.
- *Reason*: fewer moving parts for a single developer. The groups keep the runtime image lean.

## D12: mcp SDK 2.x (`MCPServer`), pinned 2.3.0
- *Alternatives*: pin `mcp<2` and use the v1 `FastMCP` API.
- *Reason*: 2.x is the current major version (FastMCP was renamed `MCPServer`); its `Client` connects
  in-process to a server instance, which makes MCP-level unit tests cheap. Pinned exactly.

## D13: uv installed with `pip install --user uv`
- *Alternatives*: the official PowerShell installer script.
- *Reason*: running a downloaded script was blocked by the local safety policy; the PyPI wheel is the
  same binary. CI installs the same pinned version (`uv==0.13.9`).

## D14: Fixed valuation date and UTC timestamps in the wealth data
- *Alternatives*: dates relative to "today"; naive datetimes.
- *Reason*: a fixed `AS_OF = 2026-10-30` keeps data identical across days (reproducible evaluation).
  Timestamps carry UTC because JSON Schema `date-time` needs an offset: the MCP Inspector rejected
  naive ones (bug found in the Phase 1 manual check, regression test added).

## D15: Wealth tool logic in `WealthService`, MCP layer kept thin
- *Alternatives*: logic directly inside the `@tool` functions.
- *Reason*: plain-Python unit tests, and the same logic can be reused by evaluation scripts.
  `transfer_funds` validates and returns the would-be balances but never mutates data.
  `get_client` also accepts a unique name fragment so the agent can find a client from a question.

## D16: pre-commit uses local hooks via `uv run`
- *Alternatives*: pinned hook repos (ruff-pre-commit, mirrors-mypy).
- *Reason*: one source of versions (uv.lock); hooks and CI run the exact same tools.

## D17: Docker Postgres host port configurable (`POSTGRES_PORT`, 5433 locally)
- *Alternatives*: stop the native PostgreSQL of another project; keep 5432.
- *Reason*: on the dev laptop a native PostgreSQL (another project) also listens on 0.0.0.0:5432 and
  silently took the connections. Compose defaults to 5432 (CI unchanged); the local `.env` uses 5433.

## D18: Gateway is a low-level `Server`; downstream results pass through unchanged
- **Decision**: the facade uses `mcp.server.lowlevel.Server` with dynamic `on_list_tools`/`on_call_tool`.
  Tools keep their description, schemas and annotations, only renamed `<server>__<tool>`. The
  downstream `CallToolResult` is returned as is (including its `_meta`). The agent's `_meta` is kept
  in `CallContext.meta` for middlewares (D4) but not forwarded downstream; SDK-reserved keys are dropped.
- *Alternatives*: `MCPServer` with generated wrapper functions; forwarding `_meta`.
- *Reason*: dynamic tool lists need the low-level API; byte-identical results are the Phase 2 test.

## D19: Audit = two writes (upsert), and failures fail closed
- **Decision**: `on_request` writes the row `in_progress` before the downstream call; `on_complete`
  upserts the final state (`INSERT ... ON CONFLICT (id) DO UPDATE`), so calls denied before the audit
  step are still recorded. If the request-side write fails, the call is refused (`error`). Unknown tool
  names go through the pipeline too, so they are audited. Downstream `isError` results get status `error`.
- *Alternatives*: a single write at the end; fail open when the DB is down.
- *Reason*: a crash mid-call still leaves a trace; an unaudited high-risk call is worse than a refused one.

## D20: One process = one long-lived client session per downstream server
- **Decision**: `DownstreamManager` opens one `mcp.Client` per enabled server at startup (HTTP or stdio,
  plus in-process servers for tests) and discovers tools once (`refresh()`, pagination followed).
- *Reason*: no per-call handshake latency. Tool-list refresh on `list_changed` is left for later.

## D21: Approval and security-event rows reference the call; only events have a foreign key
- **Decision**: `approval.audit_call_id` is an indexed UUID without a foreign key; `security_event.audit_call_id`
  has one (`ON DELETE CASCADE`). Call events are collected in `ctx.extra["security_events"]` and written by
  the audit middleware in the same transaction as the final audit row. Description events have no call.
  Audit, approvals and events share one `GatewayStore` (one connection pool, `gateway/store.py`).
- *Alternatives*: write the audit row before the policy step; deferrable constraints.
- *Reason*: the policy step (and its approval) runs before the audit step, so the audit row may not exist
  yet when the approval is created; events are written after the row, so their FK always holds.

## D22: Policies = per-agent allow lists (chosen by the user, 2026-11-18)
- **Decision**: `config/policies.yaml` maps each agent to `allow` and `require_approval` glob lists on public
  tool names (`fnmatchcase`). `require_approval` wins over `allow`; everything else is denied (default deny,
  unknown agents included). Agents only see tools they may call in `tools/list`.
- *Alternatives*: ordered rules with argument conditions (e.g. "transfers above 10,000 need approval").
- *Reason*: simpler to read and explain; argument conditions were not needed for the DONE criteria.

## D23: Presidio with `en_core_web_sm`, pattern entities only, per JSON value with field-name context
- **Decision**: the small spaCy model (12 MB, chosen by the user) as a pinned wheel URL. Default entities:
  IBAN, email, phone, credit card, US SSN (no PERSON, so client names stay usable by the assistant). When
  all entities are pattern-based, spaCy pipes are disabled and strings without a digit or `@` are skipped.
  Each value is analysed with its field name as Presidio context; findings below `min_score` 0.5 are kept.
- *Alternatives*: `en_core_web_lg` (~400 MB); analysing the whole serialized response.
- *Reason*: measured ~1 s per large response with the full pipeline vs ~0.1-0.25 s; per-value analysis
  with context avoids false hits on IDs (`ORD-214408183` scored 0.05 as SSN, `STL-…` 0.4 as phone) while a
  phone under the key `phone` scores 0.75. Known gap: ~13% of Faker phone formats (`001-…x42940`) are missed.

## D24: Rule detector = weighted regexes combined with noisy-OR, on normalized text
- **Decision**: NFKC, zero-width/bidi characters removed, Unicode tag characters decoded (ASCII smuggling),
  lower case; 13 weighted rules + a hidden-character signal; `score = 1 - prod(1 - w)`. Default threshold
  0.5. Actions: descriptions `hide` (also blocks calls), arguments `block`, responses `sanitize` (per value).
- *Alternatives*: a single keyword list; scoring the whole response.
- *Reason*: explainable scores in [0, 1] that the Phase 6 detectors can be compared with; weak signals
  ("urgent:", money movement) need company. **Caveat**: the rules were written knowing the malicious
  server's payloads; the fair measurement is Phase 6 on held-out public datasets.

## D25: Admin REST API protected by its own key
- **Decision**: `/api/approvals` requires `Authorization: Bearer $ADMIN_TOKEN` (env var named by
  `admin_token_env`); without it the API answers 503. Agent keys are not admin keys.
- *Reason*: an unauthenticated approval endpoint would let anyone approve a transfer.
