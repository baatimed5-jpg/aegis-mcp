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

## D26: Agents live in `aegis_agents/` (not `agents/`)
- **Decision**: the planned `agents/` folder is named `aegis_agents/` (assistant now, red team in Phase 8).
- *Alternatives*: keep `agents/` (it shadows the OpenAI Agents SDK, whose import name is `agents`).
- *Reason*: with the repo root on `sys.path`, `import agents` would load our folder instead of the SDK.

## D27: Benchmark = 50 generated tasks, automatic checks first, LLM judge only for free text
- **Decision**: `evaluation/build_tasks.py` builds the tasks from the seeded dataset with the same
  `WealthService` the server uses (seed 42); expected answers are computed, never typed. 18 categories
  (lookups, multi-step comparisons, transactions, simulated transfers, a non-existent client).
  Checks: `number` (rel 0.5 % for money, ±0.051 for percentages), `text` (all groups present, dates
  in several spellings), `judge` (5 free-form tasks only). A judge error counts as a failure.
- *Alternatives*: hand-written answers (error-prone, not reproducible); LLM judge for everything (noisy, costly).
- *Reason*: reproducible, cheap and defensible; a unit test proves each expected value passes its own checker.
- *Limit*: a number check passes if the right number appears anywhere in the answer.

## D28: Baseline model and settings
- **Decision**: assistant + judge = `gemini/gemini-3.5-flash` via LiteLLM (2026-11-18: `gemini-2.5-flash`
  answers 404 "no longer available to new users"; 3.6/3.7/3.8-flash answered 503 "high demand"). Gemini 3.x rejects `temperature`, so it is not sent;
  `seed=42` is sent. Runs are therefore not fully deterministic, which is why each profile runs 3 times.
- *Reason*: Flash is the spec's default; the model id is one line in `config/eval_profiles.yaml`.

## D29: Baseline profiles, approvals and run tagging
- **Decision**: Phase 4 baseline = `no_gateway` (direct to the wealth server) and `security_only` (gateway
  with the Phase 3 pipeline, no optimizer). Transfers keep requiring approval; during evaluation an
  auto-approver approves them through the admin REST API, so the approval path is measured, not skipped.
  The runner creates an `experiment_run` row and sends its id in `_meta["aegis/run_id"]`; the gateway
  stores it in `audit_call.run_id`, which also proves `_meta` reaches the gateway (D4 verified, Phase 4).
- *Reason*: both are "no optimization" configurations; Phase 5 compares the optimizer against them.

## D30: Free-tier quota strategy: offline replay for tokens, live runs for task success
- **Decision**: the free Gemini tier allows 500 requests/day for the assistant model (measured
  2026-11-19, `GenerateRequestsPerDayPerProjectPerModel-FreeTier`). Token savings and gateway overhead
  of every profile are measured **offline** by replaying the reference tool calls of the 50 tasks
  (`evaluation/oracle.py`, `evaluation/replay.py`): deterministic, exact, no LLM. Task success (needs the
  LLM) is measured live with the resumable runner; `scripts/run_live_evals.sh` continues each day.
  Offline helper LLM work (labeling, red team, LLM detector) uses `gemini-3.1-flash-lite`, whose quota is
  separate, so it never competes with the assistant model.
- *Reason*: honest numbers for every configuration tonight, without switching the assistant model mid-study.

## D31: Optimizer = pruning rules + compact tables + evidence recovery + downstream cache
- Rules (`config/pruning_rules.yaml`) drop fields per tool (`list[].field` syntax). Removed data is saved
  (Redis `omitted:<ref>`, 15 min) and the virtual tool `aegis__fetch_omitted` returns it, so a too
  aggressive rule costs one extra call, not a wrong answer. `compact` serialization turns homogeneous
  lists of objects into `{columns, rows}` (lossless). The cache stores raw downstream results of
  `readOnlyHint` tools only; hits still run PII/injection/pruning (D3). Virtual tools go through the
  normal pipeline (auth, policy, audit, PII).

## D32: Metrics middleware counts tokens of the model-visible text (tiktoken o200k_base)
- `tokens_before` = result text before the optimizer, `tokens_after` = final text; cost saved =
  difference x input price of the agent model (config). Provider-reported usage of the assistant run is
  recorded separately by the evaluation runner.

## D33: CPU PyTorch
- The GPU wheel (~2.5 GB) could not be downloaded in time (~300 KB/s link). DeBERTa-v3-small is fine-tuned
  on CPU (12 cores); the same script uses CUDA when available. The Docker image needs CPU torch anyway.

## D34: Detector selection and calibration
- `injection_scan.detector`: rules | transformer | llm | combined (D39). The transformer and the combined detector are
  calibrated so that the threshold chosen on validation maps to 0.5, so `threshold: 0.5` always means
  "the model's validated operating point". Combined = logistic regression over rules + DeBERTa scores,
  fitted on validation only. The LLM detector is batched (25 texts/request) and evaluated offline; it is
  not the default in the gateway (latency and quota).

## D35: Learned pruning: LLM labels, task-level split, recall-first threshold, rule fallback
- Labels from an LLM-as-labeler over every reference call (field needed or not). Split by task (30/10/10)
  so test questions are unseen. Features: question/field-name embedding similarity (all-MiniLM-L6-v2),
  lexical overlap, tool, value kind/size, field id. LR vs LightGBM; the model with the best validation
  PR-AUC is kept; threshold = highest one keeping >= 95 % of needed fields on validation.
  Without `_meta` question (D4) or for an unknown tool, the rule pruner is used.

## D36: Attack evaluation through the real pipeline
- Each red-team text is returned by an in-process test tool and passes the configured response pipeline;
  a call is "blocked" when the gateway records a security event. Round 2 = adaptive attacker (rewrites
  what the rules caught). Benign set = finance texts with risky words + 100 real dataset comments.

## D37: Dashboard API
- Read-only admin routes (`/api/stats|audit|security-events|policies|pipeline|experiments`) share the
  admin key with `/api/approvals`; live audit rows are pushed over `WS /api/ws/audit?token=` (browsers
  cannot set headers on WebSockets), closing with 4401 on a bad key. CORS only for the dashboard origin.

## D38: Docker demo uses compress -> filter and the rule detector
- The replay showed the same savings with half the overhead when the optimizer runs before the PII and
  injection scans (they scan less text). The demo image stays small (no ML libraries); the ML detector and
  learned pruning are enabled by switching two config values after installing the `ml` group.

## D39: Transformer detector = fine-tuned MiniLM-L6 on this machine (DeBERTa script kept)
- **Decision**: the planned DeBERTa-v3-small fine-tuning did not finish one epoch in 90 minutes on this
  laptop's CPU (no usable GPU wheel, D33; memory pressure stopped the first attempt). The same training
  script (`ml/injection/train.py`, unchanged data, splits, seed and validation-tuned threshold) fine-tunes
  `sentence-transformers/all-MiniLM-L6-v2` (6 layers, fast tokenizer) instead. The detector is now called
  `transformer`; the base model is recorded in `ml/artifacts/injection-transformer/aegis_meta.json`.
- *Alternatives*: wait for DeBERTa (hours, risk of more memory kills); a pre-trained public injection model
  (not our training, ~700 MB download at ~300 KB/s).
- *Reason*: a fine-tuned transformer trained on our data tonight. `--model microsoft/deberta-v3-small`
  reproduces the original plan on a GPU (e.g. the Colab notebook).

## D40: Tool descriptions are scanned with the rule detector; tool results/arguments with the ML detector
- **Finding**: the fine-tuned transformer (F1 0.96 on held-out texts) flags 5 of the 6 real wealth tool
  descriptions (raw score ~0.99): API docs mention "transfer", "HIGH RISK", argument instructions, the
  vocabulary it only saw inside attacks. Measured in `evaluation/attacks.py` (column
  `content_detector_flags_wealth_descriptions`), even after adding benign templated descriptions to training.
- **Decision**: `injection_scan.description_detector: rules` (default) for descriptions/schemas, which are
  static and come from configured servers; the configured ML detector scans tool results and arguments,
  where indirect injections arrive. `same` restores one detector everywhere.
- *Reason*: hiding legitimate tools would break the agent; this is a domain-shift limitation to report.
