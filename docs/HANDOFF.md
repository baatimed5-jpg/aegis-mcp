# Handoff

**Location:** `Desktop/AI Projects/3-aegis-mcp` (moved 2026-11-19; fresh `.venv`, compose project name pinned).

**State (2026-11-19 night):** user asked to run ALL remaining phases without stopping. Code for phases 4-10 is
written and committed (abb1eee + later commits). Remaining work is mostly *running* things (see below).

## What is done
- Phase 4: `aegis_agents/assistant` (Agents SDK + LiteLLM, `_meta` question/run id: D4 verified), 50 tasks
  (`evaluation/build_tasks.py`), checkers + judge, resumable `evaluation/runner.py`, `evaluation/report.py`.
- Phase 5: `gateway/optimizer/*`, `gateway/middlewares/optimizer.py` (optimizer + metrics), cache, evidence
  store + virtual tool `aegis__fetch_omitted`, `config/profiles/*.yaml`, offline `evaluation/replay.py`.
- Phase 6: `ml/injection/{synth,data,train,evaluate}.py`, detectors `transformer|llm|combined` (D34).
- Phase 7: labels (`ml/pruning/label.py`), `ml/pruning/train.py`, `gateway/optimizer/{features,learned}.py`.
- Phase 8: `aegis_agents/redteam/agent.py` (100 + adaptive), `evaluation/attacks.py`, `evaluation/final_report.py`.
- Phase 9: `dashboard/` (Angular 22) + `gateway/api/dashboard.py`, `gateway/hub.py` (WS live audit).
- Phase 10: Dockerfile, docker-compose (full stack), README, DATASETS.md, REPORT_NOTES.md, D26-D38.

## How to run
- PATH: uv at %APPDATA%\Python\Python312\Scripts (or call `.venv/Scripts/python` directly).
- Model = `gemini/gemini-3.5-flash-lite` (assistant + judge, 500 req/day free); helpers use
  `gemini/gemini-3.1-flash-lite` (separate quota). Key in `.env` (created in AI Studio, project "Default Gemini Project").
- Live evals: `bash scripts/run_live_evals.sh 08:55` (waits for the quota reset, resumes, writes REPORT.md).
- Offline: `python -m evaluation.replay` · `python -m ml.injection.evaluate` · `python -m evaluation.attacks` ·
  `python -m evaluation.final_report`. Tests: 205 pass (unit + integration).

## Remaining
Live results so far: baseline no_gateway 138/150, security_only 135/150; rule_optimizer 47/50.
Left: learned_optimizer + full. Only live LLM runs (free quota 500 req/day): `bash scripts/run_live_evals.sh 08:55` (resumes; stop the
compose app containers first, they use ports 8000/8001: `docker compose stop gateway wealth dashboard`).
Then `python -m evaluation.report --tag baseline` / `--tag optimizers` and commit.
Done: detector (MiniLM, D39), attacks, replay, report, docker compose verified (WS via nginx, D40 split).

## Known issues
- Free quota limits live runs (D30); learned-pruning replay includes its training tasks (clean comparison = held-out table).
- CPU-only torch (D33). Approvals in-memory (D7).
