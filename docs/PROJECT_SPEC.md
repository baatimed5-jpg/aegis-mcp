# Project: AegisMCP: An Intelligent MCP Gateway with Learned Context Optimization
# and ML-Based Prompt-Injection Detection for LLM Agents

## 0. How you must work (read first)
- This is my final-year engineering project (PFE). I work alone, without a supervisor.
  Act as my technical lead: briefly explain important design decisions so I can defend them.
- Work strictly phase by phase (section 9). At the end of each phase: run all tests, show results,
  then follow the end-of-phase steps in section 0.1 and STOP.
- Never skip, fake, or hard-code results. If a test, training run, or experiment fails, tell me.
  Never invent evaluation numbers.
- Do not guess library APIs. Before using a library, check the installed version's documentation
  or source code (site-packages). Pin all dependency versions.
- Do not add features or technologies not listed here without asking me first.
- If something in this prompt is ambiguous or impossible, ask me before continuing.
- Record every important design decision in docs/DECISIONS.md (decision, alternatives, reason).

## 0.1 Session management (save tokens; very important)
I use the Claude Pro plan, which has limited usage, so every phase runs in a NEW session.

In Phase 0, create these files:
- docs/PROJECT_SPEC.md: this full prompt, copied exactly as written.
- CLAUDE.md (at the repo root, under 60 lines): project summary, tech stack, folder structure,
  commands (install, test, lint, run), engineering rules, and this instruction:
  "At session start, read docs/PROGRESS.md and docs/HANDOFF.md, then ONLY the section of
  docs/PROJECT_SPEC.md for the current phase. Do not re-read the whole repo."
- docs/PROGRESS.md: a checklist of all phases, marked done or not done.
- docs/HANDOFF.md: notes for the next session.

At the END of every phase (after tests pass and before stopping):
1. Commit all work to Git.
2. Update docs/PROGRESS.md.
3. Rewrite docs/HANDOFF.md (max 40 lines) with: the phase just finished, the next phase,
   files created or changed, how to run and test, known issues, open decisions,
   and the exact first steps for the next phase.
4. Then print this message to me, exactly:
   "✅ Phase X done. Start a NEW session to save tokens:
    1) Type /clear (or close and reopen Claude Code)
    2) Paste: Continue the project. Read CLAUDE.md, docs/PROGRESS.md and docs/HANDOFF.md, then start Phase Y."
   (Replace X and Y with the real phase numbers.)

If my usage limit is close to running out in the middle of a phase, stop at a safe point,
commit, update HANDOFF.md with exactly where you stopped, and print the same message
with "continue Phase X" instead of "start Phase Y".

Token-saving rules for every session:
- Read only the files needed for the current task; use search (grep/glob) instead of
  opening whole folders.
- Don't re-read files you already read in this session unless they changed.
- Keep your explanations short; put long details in docs/ files instead of the chat.
- Run only the tests for the code you changed while working; run the full suite once at the end of the phase.

## 1. Problem
AI agents access tools and data through MCP (Model Context Protocol) servers. Two problems:
1. Cost: tool responses are large and contain many fields the LLM does not need, which wastes
   tokens and money on every call.
2. Security: tool descriptions and tool responses can contain prompt injections that
   hijack the agent, and agents often have more access than they need.

## 2. Solution overview
A gateway that sits between AI agents and MCP servers:
- To agents, the gateway IS an MCP server (agents connect only to it).
- To real MCP servers, the gateway IS an MCP client.
- It aggregates several downstream servers and exposes their tools with a namespace:
  `<server_name>__<tool_name>` (example: `wealth__get_holdings`).
- Every call passes through a configurable middleware pipeline:

REQUEST (agent → server):
  1. AuthMiddleware: identify the agent (Bearer token over HTTP).
  2. PolicyMiddleware: allow/deny by agent, tool, and argument rules from YAML; tools marked
     `requires_approval` are held until approved in the dashboard (timeout 120 s → deny).
  3. InjectionScanMiddleware: scan tool descriptions (at discovery) and arguments.
  4. AuditMiddleware: store the call in PostgreSQL.

RESPONSE (server → agent):
  5. PIIMaskMiddleware: mask sensitive data with Microsoft Presidio.
  6. InjectionScanMiddleware: scan returned content; block or sanitize per config.
  7. OptimizerMiddleware: reduce tokens (section 4).
  8. MetricsMiddleware: tokens before/after, cost saved, latency.

Each middleware implements one common interface, can be enabled/disabled in config,
and the order is defined in config (needed for experiments).

## 3. Main contribution A: ML-based injection detector
Build and compare 4 detectors on the SAME held-out test set:
  a) Rules: regex/heuristics.
  b) Fine-tuned small transformer classifier (start with `microsoft/deberta-v3-small`,
     must be trainable on CPU or a free Colab GPU; provide a Colab-ready training notebook).
  c) LLM-as-classifier (zero-shot prompt via the configured LLM).
  d) Combined: a weighted score of a, b, c.
Data: public prompt-injection datasets from Hugging Face (check and record each dataset's license
in docs/DATASETS.md; ask me if a license is unclear) + synthetic examples tailored to
financial tool outputs. Split train/validation/test with a fixed random seed; no leakage.
Report precision, recall, F1, false-positive rate, and latency per detector.

## 4. Main contribution B: learned context optimizer
Strategies, each toggleable:
  1. Rule-based field pruning: per-tool YAML of fields to keep.
  2. Learned field pruning: a model predicts, for (user question, tool, field), whether the
     LLM needs that field.
     - Labels: run the assistant agent on benchmark tasks with full responses; ask the LLM
       to list which fields it used (LLM-as-labeler); store the labels.
     - Features: embedding similarity between question and field name/value, field type,
       tool name, value length.
     - Model: start simple (logistic regression or LightGBM); keep a recall-first threshold
       so needed fields are rarely removed.
  3. Compact serialization: JSON vs compact table format; measure which saves more tokens.
  4. Response cache in Redis (key = tool + normalized arguments, TTL configurable).
  5. Evidence recovery: pruned fields are stored server-side; the response contains
     `{"_omitted": [field names], "_ref": "<id>"}` and the gateway exposes the tool
     `gateway__fetch_omitted(ref_id, fields)` so the agent can get them if needed.
Token counting: tiktoken for fast estimates + the provider's official token-count API for
reported numbers.

## 5. Components
1. servers/wealth: demo MCP server with FICTIONAL wealth-management data (Faker, fixed seed):
   clients, advisors, portfolios, holdings, transactions. Tools: get_client, list_portfolios,
   get_holdings, get_transactions, get_portfolio_performance, transfer_funds (high-risk,
   simulated only). Responses must be realistically large (many fields) so optimization matters.
2. servers/malicious: MCP server with poisoned tool descriptions and responses containing
   injections, used only for testing.
3. gateway/: core proxy, pipeline, middlewares, REST + WebSocket API for the dashboard.
4. ml/: injection detector training + learned pruning training, with saved model artifacts.
5. agents/assistant: wealth-advisor assistant agent that uses the gateway.
6. agents/redteam: small agent that generates new attack variants for testing (secondary feature).
7. evaluation/: benchmark runner and report generator.
8. dashboard/: Angular admin UI.

## 6. Tech stack (pin exact versions in pyproject.toml / package.json)
- Python 3.12, uv for dependency management
- MCP: official MCP Python SDK (`mcp` package); transports: Streamable HTTP (main) and stdio
- API: FastAPI, Pydantic v2, WebSocket
- Agents: OpenAI Agents SDK with its LiteLLM model integration (to run on Gemini/Claude);
  if that integration does not work with the installed versions, use PydanticAI and record why
- LLM providers via LiteLLM, configured in .env:
  LLM_PROVIDER, LLM_MODEL, GEMINI_API_KEY, ANTHROPIC_API_KEY.
  Default Gemini Flash for high-volume calls (classifier, labeling, evaluation);
  allow per-component model override in config (e.g. Claude for the red-team agent)
- ML: PyTorch, Hugging Face transformers + datasets, scikit-learn, LightGBM,
  sentence-transformers (embeddings)
- Security: Microsoft Presidio (PII), PyJWT or API keys
- Storage: PostgreSQL 16 (SQLAlchemy 2 + Alembic), Redis 7
- Evaluation/reporting: pandas, matplotlib
- Frontend: Angular (latest stable) + one chart library
- Quality: pytest + pytest-asyncio + coverage, ruff, mypy (strict on gateway/), pre-commit
- Infra: Docker, Docker Compose, GitHub Actions CI (lint + type check + tests)

## 7. Ports and config
gateway 8000 | wealth server 8001 | malicious server 8002 | dashboard 4200 |
postgres 5432 | redis 6379.
All settings in config/gateway.yaml + .env. Secrets ONLY in the project's .env file (never as
system environment variables); .env is in .gitignore; provide .env.example with placeholder
values. Never print keys in logs.

## 8. Evaluation (evaluation/)
- Benchmark: 50 realistic assistant tasks with expected answers (checked automatically where
  possible, LLM-judge only when needed) + 100 attacks (from malicious server and red-team agent)
  + benign samples to measure false positives.
- Configurations to compare: (1) no gateway, (2) security only, (3) rule-based optimizer only,
  (4) learned optimizer only, (5) full gateway, (6) filter→compress vs compress→filter order.
- Metrics (exact definitions):
  token_savings = (tokens_before − tokens_after) / tokens_before
  cost_saved = token difference × provider price per token (prices in config)
  task_success_rate = correct tasks / total tasks
  attack_block_rate = blocked attacks / total attacks
  false_positive_rate = blocked benign calls / total benign calls
  latency overhead = gateway p50 / p95 added milliseconds
- Run each configuration 3 times; report mean and standard deviation.
- Output: results CSV + charts (PNG) + evaluation/REPORT.md generated from the real results.

## 9. Phases and definition of done
Phase 0: Plan. docs/ARCHITECTURE.md with Mermaid diagrams (components, request/response
  sequence, data model), folder structure, task list, plus the session files from section 0.1
  (CLAUDE.md, PROJECT_SPEC.md, PROGRESS.md, HANDOFF.md). No application code.
  DONE when I approve it.
Phase 1: Repo setup (uv, ruff, mypy, pytest, pre-commit, CI, docker-compose with Postgres
  and Redis) + wealth MCP server + fake data. DONE when tests pass and I can call its tools
  with the MCP Inspector.
Phase 2: Gateway pass-through proxy (aggregation, namespacing, both transports), middleware
  pipeline, audit log. DONE when an agent can call wealth tools through the gateway
  with identical results and every call is in the audit table.
Phase 3: Auth, policies, approval queue, PII masking, rule-based injection scan, malicious
  server. DONE when tests prove: unauthorized calls are denied, policy rules work, PII is masked,
  known injections from the malicious server are caught.
Phase 4: Assistant agent + benchmark tasks + baseline evaluation (no optimization).
  DONE when the baseline report exists with real numbers.
Phase 5: Optimizer: rule-based pruning, compact format, cache, evidence recovery, metrics.
  DONE when evaluation shows token savings and task success compared with the baseline.
Phase 6: ML injection detector (datasets, training notebook, 4-detector comparison) and
  integration into the gateway. DONE when the comparison table is produced on the test set.
Phase 7: Learned pruning (labeling, training, integration, comparison with rule-based).
  DONE when the comparison results are produced.
Phase 8: Red-team agent + full evaluation of all configurations + charts + REPORT.md.
Phase 9: Angular dashboard (live audit log via WebSocket, blocked attacks, tokens/cost saved,
  approval queue, policy viewer, experiment charts). DONE when it runs with docker compose
  and shows live data.
Phase 10: Final polish: `docker compose up` runs everything, README (setup, architecture,
  screenshots, how to reproduce results), docs/REPORT_NOTES.md to help me write my PFE report.

## 10. Engineering rules
- Clean, modular code; type hints everywhere; docstrings; async for I/O.
- Unit tests for every module + integration tests for the gateway (real Postgres/Redis
  via docker compose or testcontainers, not only mocks). Keep the suite green.
- Fixed random seeds for data generation, splits, and training (reproducibility).
- Initialize Git at Phase 1; commit after each meaningful step with clear messages;
  never commit .env, model weights over 50 MB, or datasets (document how to download them).
- Prefer simple working solutions first, then improve.

## Start
Begin with Phase 0 only.
