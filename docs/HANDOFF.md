# Handoff

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
  `python -m evaluation.final_report`. Tests: 187 unit + 16 integration pass.

## Remaining
1. DeBERTa training (`ml/train_deberta.log`) -> `ml.injection.evaluate` -> `evaluation.attacks` -> replay all 6
   profiles -> `evaluation.final_report` -> commit.
2. `docker compose up --build` smoke test (gateway healthz, dashboard login, live WS).
3. Live runs over several days (quota): baseline security_only, then rule/learned/full.

## Known issues
- Free quota limits live runs (D30); learned-pruning replay includes its training tasks (clean comparison = held-out table).
- CPU-only torch (D33). Approvals in-memory (D7).
