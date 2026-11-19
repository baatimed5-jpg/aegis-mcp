"""Build the evaluation report from the runner's CSV files (no number is typed by hand).

    uv run python -m evaluation.report --tag baseline

Reads ``evaluation/results/<tag>/tasks.csv`` (+ ``gateway_calls.csv``), writes
``evaluation/results/<tag>/summary.csv`` and the ``<tag>`` section of ``evaluation/REPORT.md``.

Metrics (spec §8): task_success_rate = correct / total, per run, then mean and std over runs.
Tokens and cost are the provider-reported usage of the assistant (all LLM requests of a task);
cost uses the prices in ``config/eval_profiles.yaml``. Gateway overhead = ``latency_total_ms -
latency_downstream_ms`` of each audited call (p50/p95); client-side latency compares tool calls
seen by the agent with and without the gateway.
"""

from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from evaluation.runner import is_infra_error

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "evaluation" / "results"
REPORT = ROOT / "evaluation" / "REPORT.md"
PROFILES_FILE = ROOT / "config" / "eval_profiles.yaml"


def read_csv(path: Path) -> list[dict[str, str]]:
    """Rows of a CSV file, or an empty list if it does not exist."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolation percentile (q in [0, 100]); NaN for an empty list."""
    if not values:
        return float("nan")
    data = sorted(values)
    k = (len(data) - 1) * q / 100
    lo, hi = int(k), min(int(k) + 1, len(data) - 1)
    return data[lo] + (data[hi] - data[lo]) * (k - lo)


def mean_std(values: list[float]) -> tuple[float, float]:
    """Mean and sample standard deviation (0 for a single value)."""
    if not values:
        return float("nan"), float("nan")
    return statistics.fmean(values), statistics.stdev(values) if len(values) > 1 else 0.0


def task_cost(row: dict[str, str], price: dict[str, float]) -> float:
    """USD cost of one task from its token usage."""
    return (int(row["input_tokens"]) * price["input"] + int(row["output_tokens"]) * price["output"]) / 1e6


def summarize(rows: list[dict[str, str]], price: dict[str, float]) -> dict[str, dict[str, Any]]:
    """Per-profile metrics: per-run values, then mean/std over runs."""
    latest = {(r["profile"], r["run_index"], r["task_id"]): r for r in rows}  # resumed tasks: last row wins
    by_run: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    for r in latest.values():
        by_run[r["profile"]][r["run_index"]].append(r)
    out: dict[str, dict[str, Any]] = {}
    for profile, runs in by_run.items():
        per_run: dict[str, list[float]] = defaultdict(list)
        latencies: list[float] = []
        for run_rows in runs.values():
            n = len(run_rows)
            per_run["task_success_rate"].append(sum(r["correct"] == "True" for r in run_rows) / n)
            per_run["input_tokens_per_task"].append(
                statistics.fmean(int(r["input_tokens"]) for r in run_rows)
            )
            per_run["output_tokens_per_task"].append(
                statistics.fmean(int(r["output_tokens"]) for r in run_rows)
            )
            per_run["tool_output_tokens_per_task"].append(
                statistics.fmean(int(r["tool_output_tokens"]) for r in run_rows)
            )
            per_run["llm_requests_per_task"].append(
                statistics.fmean(int(r["llm_requests"]) for r in run_rows)
            )
            per_run["tool_calls_per_task"].append(statistics.fmean(int(r["tool_calls"]) for r in run_rows))
            per_run["cost_usd_per_task"].append(statistics.fmean(task_cost(r, price) for r in run_rows))
            per_run["errors"].append(float(sum(is_infra_error(r["error"]) for r in run_rows)))
            per_run["wall_s_per_task"].append(statistics.fmean(float(r["wall_s"]) for r in run_rows))
            for r in run_rows:
                latencies += [float(v) for v in r.get("tool_latencies_ms", "").split()]
        categories: dict[str, list[bool]] = defaultdict(list)
        for run_rows in runs.values():
            for r in run_rows:
                categories[r["category"]].append(r["correct"] == "True")
        out[profile] = {
            "runs": len(runs),
            "tasks_per_run": sorted({len(v) for v in runs.values()}),
            "metrics": {k: mean_std(v) for k, v in per_run.items()},
            "client_tool_latency_ms": (percentile(latencies, 50), percentile(latencies, 95), len(latencies)),
            "categories": {c: sum(v) / len(v) for c, v in sorted(categories.items())},
        }
    return out


def gateway_overhead(calls: list[dict[str, str]]) -> dict[str, tuple[float, float, int, dict[str, int]]]:
    """Per profile: p50/p95 of gateway-added ms, number of calls, and calls per status."""
    by_profile: dict[str, list[dict[str, str]]] = defaultdict(list)
    for c in calls:
        by_profile[c["profile"]].append(c)
    out = {}
    for profile, rows in by_profile.items():
        added = [
            float(r["latency_total_ms"]) - float(r["latency_downstream_ms"])
            for r in rows
            if r["latency_total_ms"] and r["latency_downstream_ms"]
        ]
        statuses: dict[str, int] = defaultdict(int)
        for r in rows:
            statuses[r["status"]] += 1
        out[profile] = (percentile(added, 50), percentile(added, 95), len(rows), dict(statuses))
    return out


METRIC_LABELS = [
    ("task_success_rate", "Task success rate", "{:.1%}"),
    ("input_tokens_per_task", "Input tokens / task", "{:,.0f}"),
    ("output_tokens_per_task", "Output tokens / task", "{:,.0f}"),
    ("tool_output_tokens_per_task", "Tool-output tokens / task (tiktoken)", "{:,.0f}"),
    ("llm_requests_per_task", "LLM requests / task", "{:.2f}"),
    ("tool_calls_per_task", "Tool calls / task", "{:.2f}"),
    ("cost_usd_per_task", "Cost / task (USD)", "{:.5f}"),
    ("wall_s_per_task", "Wall time / task (s)", "{:.1f}"),
    ("errors", "Tasks lost to provider/infra errors / run", "{:.1f}"),
]


def fmt(pair: tuple[float, float], pattern: str) -> str:
    """``mean ± std`` with the metric's format."""
    m, s = pair
    return f"{pattern.format(m)} ± {pattern.format(s)}"


def render(
    tag: str, meta: dict[str, Any], summary: dict[str, dict[str, Any]], overhead: dict[str, Any]
) -> str:
    """Markdown section for this result tag."""
    profiles = list(summary)
    lines = [
        f"<!-- section:{tag} -->",
        f"## {tag.capitalize()} results",
        "",
        f"Generated by `python -m evaluation.report --tag {tag}` from `evaluation/results/{tag}/`.",
        f"Model: `{meta.get('model', '?')}` · judge: `{meta.get('judge_model', '?')}` · "
        f"runs per profile: {', '.join(f'{p}={summary[p]["runs"]}' for p in profiles)} · "
        f"tasks per run: {', '.join(f'{p}={summary[p]["tasks_per_run"]}' for p in profiles)}.",
        "Values are mean ± standard deviation over runs.",
        "",
        "| Metric | " + " | ".join(profiles) + " |",
        "|---|" + "---|" * len(profiles),
    ]
    for key, label, pattern in METRIC_LABELS:
        cells = [
            fmt(summary[p]["metrics"][key], pattern) if key in summary[p]["metrics"] else "n/a"
            for p in profiles
        ]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    cells = []
    for p in profiles:
        p50, p95, n = summary[p]["client_tool_latency_ms"]
        cells.append(f"p50 {p50:.1f} / p95 {p95:.1f} (n={n})")
    lines.append("| Tool-call latency seen by the agent (ms) | " + " | ".join(cells) + " |")
    lines += ["", "### Gateway overhead (from the audit log)", ""]
    if overhead:
        lines += [
            "| Profile | Added ms p50 | Added ms p95 | Audited calls | Status counts |",
            "|---|---|---|---|---|",
        ]
        for p, (p50, p95, n, statuses) in overhead.items():
            status_text = ", ".join(f"{k}={v}" for k, v in sorted(statuses.items()))
            lines.append(f"| {p} | {p50:.1f} | {p95:.1f} | {n} | {status_text} |")
    else:
        lines.append("No gateway profile in this result set.")
    lines += ["", "### Success rate per task category (all runs pooled)", ""]
    cats = sorted({c for p in profiles for c in summary[p]["categories"]})
    lines += ["| Category | " + " | ".join(profiles) + " |", "|---|" + "---|" * len(profiles)]
    for c in cats:
        cells = [
            f"{summary[p]['categories'][c]:.0%}" if c in summary[p]["categories"] else "n/a" for p in profiles
        ]
        lines.append(f"| {c} | " + " | ".join(cells) + " |")
    lines += ["", f"<!-- /section:{tag} -->"]
    return "\n".join(lines)


def write_section(section: str, tag: str) -> None:
    """Insert or replace this tag's section in REPORT.md."""
    header = "# AegisMCP evaluation report\n\nEvery number below is generated from the CSV results.\n"
    text = REPORT.read_text(encoding="utf-8") if REPORT.exists() else header
    start, end = f"<!-- section:{tag} -->", f"<!-- /section:{tag} -->"
    if start in text and end in text:
        before, rest = text.split(start, 1)
        after = rest.split(end, 1)[1]
        text = before + section + after
    else:
        text = text.rstrip() + "\n\n" + section + "\n"
    REPORT.write_text(text, encoding="utf-8")


def write_summary_csv(path: Path, summary: dict[str, dict[str, Any]]) -> None:
    """Machine-readable summary (one row per profile and metric)."""
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["profile", "metric", "mean", "std", "runs"])
        for p, s in summary.items():
            for key, (m, sd) in s["metrics"].items():
                w.writerow([p, key, f"{m:.6f}", f"{sd:.6f}", s["runs"]])


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Generate the evaluation report section")
    parser.add_argument("--tag", default="baseline")
    args = parser.parse_args()
    folder = RESULTS_DIR / args.tag
    rows = read_csv(folder / "tasks.csv")
    if not rows:
        raise SystemExit(f"no results in {folder}")
    metas = sorted(folder.glob("meta_*.json"))
    meta: dict[str, Any] = yaml.safe_load(metas[-1].read_text(encoding="utf-8")) if metas else {}
    cfg = yaml.safe_load(PROFILES_FILE.read_text(encoding="utf-8"))
    model = meta.get("model", cfg["model"])
    if model not in cfg["pricing"]:
        raise SystemExit(f"no price for {model} in {PROFILES_FILE}")
    summary = summarize(rows, cfg["pricing"][model])
    overhead = gateway_overhead(read_csv(folder / "gateway_calls.csv"))
    write_summary_csv(folder / "summary.csv", summary)
    write_section(render(args.tag, meta, summary, overhead), args.tag)
    print(f"wrote {REPORT} and {folder / 'summary.csv'}")


if __name__ == "__main__":
    main()
