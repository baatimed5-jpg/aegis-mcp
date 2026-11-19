#!/usr/bin/env bash
# Offline evaluations (no assistant LLM): detector comparison, attacks, replay of all profiles, report.
# Waits for the DeBERTa model if training is still running.
set -u
cd "$(dirname "$0")/.."
PY=.venv/Scripts/python; [ -x "$PY" ] || PY=.venv/bin/python
export HF_HUB_OFFLINE=1 PYTHONUNBUFFERED=1
until [ -f ml/artifacts/injection-deberta/aegis_meta.json ]; do sleep 30; done
echo "== detector comparison" && "$PY" -m ml.injection.evaluate
echo "== attacks" && "$PY" -m evaluation.attacks
echo "== replay" && "$PY" -m evaluation.replay --repeats 3
echo "== report" && "$PY" -m evaluation.final_report
echo "offline evaluations done"
