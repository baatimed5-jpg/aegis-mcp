#!/usr/bin/env bash
# Live (LLM) evaluation queue, resumable. Free Gemini tier = 500 requests/day for the assistant model,
# so this script can be re-run every day until every profile is complete; finished tasks are skipped.
# Usage: bash scripts/run_live_evals.sh [wait_until_HH:MM]
set -u
cd "$(dirname "$0")/.."
PY=.venv/Scripts/python; [ -x "$PY" ] || PY=.venv/bin/python
if [ -n "${1:-}" ]; then
  now=$(date +%s); target=$(date -d "today $1" +%s); [ "$target" -le "$now" ] && target=$(date -d "tomorrow $1" +%s)
  echo "waiting until $1 ($(( (target-now)/60 )) min) for the daily quota reset"; sleep $(( target-now ))
fi
run() { PYTHONUNBUFFERED=1 "$PY" -m evaluation.runner --min-interval 6 "$@"; }
run --tag baseline --profiles no_gateway security_only --runs 3 && "$PY" -m evaluation.report --tag baseline
run --tag optimizers --profiles rule_optimizer learned_optimizer full --runs 1 && "$PY" -m evaluation.report --tag optimizers
run --tag optimizers --profiles rule_optimizer learned_optimizer full --runs 3 && "$PY" -m evaluation.report --tag optimizers
"$PY" -m evaluation.final_report
echo "live evaluation queue finished (or stopped by the daily quota: re-run tomorrow)"
