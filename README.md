# AegisMCP

An intelligent **MCP gateway** between LLM agents and MCP tool servers. To agents it is one MCP
server; to tools it is an MCP client. Every tool call goes through a configurable middleware pipeline:

- **Security**: API-key auth, per-agent allow lists, human approval for risky tools (money transfers),
  PII masking (Presidio), prompt-injection scanning of tool descriptions, arguments and results.
  Detectors: rules, a fine-tuned **DeBERTa** classifier, an LLM classifier, or a learned **combination**.
- **Context optimization**: rule-based or **learned** field pruning, compact table serialization,
  evidence recovery (`aegis__fetch_omitted` gives back anything pruned), and a result cache.
- **Observability**: every call audited in PostgreSQL (status, latency, tokens saved, cost saved),
  live **Angular dashboard** with approvals, blocked attacks, policies and experiment charts.

Results (all generated from real runs): [`evaluation/REPORT.md`](evaluation/REPORT.md).
Design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · decisions: [`docs/DECISIONS.md`](docs/DECISIONS.md) ·
datasets: [`docs/DATASETS.md`](docs/DATASETS.md) · report help: [`docs/REPORT_NOTES.md`](docs/REPORT_NOTES.md).

```
 LLM agent ──MCP──▶  AegisMCP gateway  ──MCP──▶  wealth server (6 tools)   [malicious test server]
 (assistant /        request:  auth → PII mask → policy/approval → injection scan → audit
  red team)          response: optimizer → PII mask → injection scan → metrics
                         │                     │
                     PostgreSQL            Redis (cache, evidence)      dashboard :4200 (REST + WebSocket)
```

## Run everything (Docker)

```bash
cp .env.example .env        # then set ADMIN_TOKEN, AGENT_TOKEN_ASSISTANT, AGENT_TOKEN_REDTEAM (any long random strings)
docker compose up --build   # postgres, redis, migrations, wealth :8001, gateway :8000, dashboard :4200
```

Open http://localhost:4200 and log in with your `ADMIN_TOKEN`. Agents connect to
`http://localhost:8000/mcp` with `Authorization: Bearer <AGENT_TOKEN_...>`.
`docker compose --profile attack up` also starts the malicious test server (enable it in the config).

## Local development (Windows / Linux)

```bash
uv sync --all-groups                      # Python 3.12, dev + agents + ml groups
docker compose up -d postgres redis
uv run alembic upgrade head
uv run python -m servers.wealth           # :8001
uv run python -m gateway                  # :8000 (config/gateway.yaml)
cd dashboard && npm ci && npm start       # :4200
```

Ask the assistant a question through the gateway (needs `GEMINI_API_KEY` in `.env`):

```bash
uv run python -m aegis_agents.assistant "Which risk profile is recorded for Curtis Brown?"
```

Tests and checks: `uv run pytest` · `uv run ruff check . && uv run mypy` · `cd dashboard && npm test`.

## Reproduce the results

| Result | Command | LLM requests |
|---|---|---|
| 50 benchmark tasks (answers computed from the data) | `uv run python -m evaluation.build_tasks` | 0 |
| Live task success, tokens, cost (per profile, 3 runs) | `bash scripts/run_live_evals.sh` (resumable, free tier = 500 req/day) | ~115 per run |
| Token savings + overhead of every profile | `uv run python -m evaluation.replay` | 0 |
| Injection data + DeBERTa + 4-detector comparison | `python -m ml.injection.data`, `python -m ml.injection.train`, `python -m ml.injection.evaluate` | ~10 (LLM detector) |
| Learned pruning: labels, training, comparison | `python -m ml.pruning.label`, `python -m ml.pruning.train` | ~70 |
| Red-team attacks + block/false-positive rates | `python -m aegis_agents.redteam.agent`, `python -m evaluation.attacks` | ~10 |
| Charts + REPORT.md | `python -m evaluation.report --tag baseline`, `python -m evaluation.final_report` | 0 |

Model artifacts (`ml/artifacts/`) are not committed when larger than 50 MB; the scripts above re-create
them. To use them in the gateway: `injection_scan.detector: combined` and `optimizer.pruning: learned`
(see `config/profiles/full.yaml`).

## Project layout

`gateway/` (pipeline, middlewares, detectors, optimizer, API) · `servers/wealth`, `servers/malicious` ·
`aegis_agents/` (assistant, red team) · `evaluation/` (tasks, runner, replay, attacks, reports) ·
`ml/` (injection detector, learned pruning) · `dashboard/` (Angular) · `config/` (gateway, policies,
pruning rules, evaluation profiles) · `tests/` (unit + integration with real Postgres/Redis).
