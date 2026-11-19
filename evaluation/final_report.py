"""Build every non-live section of evaluation/REPORT.md + charts from the result CSVs (Phase 8).

    uv run python -m evaluation.final_report

Sections: offline replay (token savings and gateway overhead of every profile), injection
detector comparison (Phase 6), learned vs rule pruning (Phase 7), attacks and false positives
(Phase 8). Live task-success sections are written by ``evaluation.report --tag <tag>``.
Missing inputs are reported as "not run yet", never filled in.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from evaluation.report import write_section

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "evaluation" / "results"


def read(path: Path) -> list[dict[str, str]]:
    """CSV rows, empty when the file does not exist."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def table(rows: list[dict[str, Any]], cols: list[str]) -> list[str]:
    """Markdown table lines."""
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(str(r.get(c, "")) for c in cols) + " |" for r in rows]
    return out


def bar(
    path: Path, labels: list[str], values: list[float], title: str, ylabel: str, pct: bool = False
) -> None:
    """Simple labelled bar chart."""
    fig, ax = plt.subplots(figsize=(8, 4))
    bars = ax.bar(labels, values, color="#3b6ea5")
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.tick_params(axis="x", rotation=20)
    for b, v in zip(bars, values, strict=True):
        ax.annotate(f"{v:.0%}" if pct else f"{v:,.1f}", (b.get_x() + b.get_width() / 2, b.get_height()),
                    ha="center", va="bottom", fontsize=8)  # fmt: skip
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def replay_section() -> str:
    """Token savings + overhead of every profile on the reference calls."""
    rows = read(RES / "replay" / "replay_summary.csv")
    lines = ["<!-- section:replay -->", "## Token savings and gateway overhead (offline replay)", ""]
    if not rows:
        return "\n".join([*lines, "Not run yet.", "<!-- /section:replay -->"])
    lines += [
        "All reference tool calls of the 50 tasks (`evaluation/oracle.py`) replayed through each profile's",
        "pipeline (`python -m evaluation.replay`). Tool results are deterministic, so token numbers are exact;",
        "`token_savings = (tokens_before - tokens_after) / tokens_before` (tiktoken o200k_base, D6).",
        "Gateway overhead = total - downstream latency per call (ms), in-process, CPU.",
        "",
        *table(
            rows,
            [
                "profile",
                "calls",
                "tokens_before",
                "tokens_after",
                "token_savings",
                "gateway_ms_p50",
                "gateway_ms_p95",
            ],
        ),
    ]
    bar(RES / "replay" / "token_savings.png", [r["profile"] for r in rows], [float(r["token_savings"]) for r in rows],
        "Token savings per profile (replay)", "share of tool-result tokens removed", pct=True)  # fmt: skip
    bar(RES / "replay" / "overhead_p50.png", [r["profile"] for r in rows], [float(r["gateway_ms_p50"]) for r in rows],
        "Gateway overhead per call (median)", "ms")  # fmt: skip
    return "\n".join([*lines, "", "<!-- /section:replay -->"])


def injection_section() -> str:
    """4-detector comparison on the held-out test set."""
    rows = read(RES / "injection" / "comparison.csv")
    lines = ["<!-- section:injection -->", "## Injection detectors (Phase 6, held-out test set)", ""]
    if not rows:
        return "\n".join([*lines, "Not run yet.", "<!-- /section:injection -->"])
    meta_path = RES / "injection" / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    summary_path = ROOT / "ml" / "data" / "injection" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    lines += [
        "Test = deepset/prompt-injections test split + synthetic financial templates never seen in training",
        f"(n={rows[0]['n_test']}). Thresholds chosen on validation only. LLM detector: {meta.get('llm', 'n/a')}.",
        f"Leakage check (shared texts/templates between splits): {summary.get('leakage', 'n/a')}.",
        "",
        *table(
            rows,
            ["detector", "threshold", "precision", "recall", "f1", "fpr", "roc_auc", "latency_ms_per_text"],
        ),
    ]
    by_source = read(RES / "injection" / "by_source.csv")
    if by_source:
        lines += [
            "",
            "By source:",
            "",
            *table(by_source, ["detector", "source", "n", "precision", "recall", "f1", "fpr"]),
        ]
    bar(RES / "injection" / "f1.png", [r["detector"] for r in rows], [float(r["f1"]) for r in rows],
        "Injection detection F1 (test)", "F1", pct=True)  # fmt: skip
    return "\n".join([*lines, "", "<!-- /section:injection -->"])


def pruning_section() -> str:
    """Learned vs rule pruning on held-out tasks."""
    rows = read(RES / "pruning" / "comparison.csv")
    lines = ["<!-- section:pruning -->", "## Learned vs rule-based pruning (Phase 7, held-out tasks)", ""]
    if not rows:
        return "\n".join([*lines, "Not run yet.", "<!-- /section:pruning -->"])
    meta = json.loads((RES / "pruning" / "meta.json").read_text(encoding="utf-8"))
    lines += [
        f"Labels: LLM-as-labeler ({meta.get('labeler')}) on every reference call; split by task "
        f"{meta.get('tasks')}; threshold = highest keeping >= {meta.get('target_recall'):.0%} of needed fields on validation.",
        "recall_needed = needed fields kept; calls_complete = calls where every needed field was kept;",
        "fields_dropped = share of candidate fields removed.",
        "",
        *table(
            rows,
            [
                "pruner",
                "threshold",
                "val_pr_auc",
                "test_pr_auc",
                "recall_needed",
                "precision_kept",
                "fields_dropped",
                "calls_complete",
            ],
        ),
        "",
        f"Selected model: `{meta.get('selected')}`. LLM labels cover {meta.get('llm_vs_heuristic_coverage')} of the "
        "fields that contain the expected answer value (sanity check).",
    ]
    return "\n".join([*lines, "", "<!-- /section:pruning -->"])


def attacks_section() -> str:
    """Attack block rate and false positives per configuration."""
    rows = read(RES / "attacks" / "results.csv")
    lines = ["<!-- section:attacks -->", "## Attacks and false positives (Phase 8)", ""]
    if not rows:
        return "\n".join([*lines, "Not run yet.", "<!-- /section:attacks -->"])
    lines += [
        "Red-team agent (`aegis_agents/redteam`): 100 LLM-written indirect injections over 5 goals x 10",
        "techniques, plus an adaptive round where every attack caught by the rules was rewritten to evade",
        "them. Each text is returned by a tool and passes the real gateway response pipeline.",
        "",
        *table(
            rows,
            [
                "config",
                "attacks",
                "attack_block_rate",
                "block_rate_round1",
                "block_rate_adaptive",
                "adaptive_n",
                "benign",
                "false_positive_rate",
            ],
        ),
    ]
    bar(RES / "attacks" / "block_rate.png", [r["config"] for r in rows], [float(r["attack_block_rate"]) for r in rows],
        "Attack block rate", "blocked attacks / attacks", pct=True)  # fmt: skip
    bar(RES / "attacks" / "false_positives.png", [r["config"] for r in rows], [float(r["false_positive_rate"]) for r in rows],
        "False positive rate (benign texts)", "blocked benign / benign", pct=True)  # fmt: skip
    return "\n".join([*lines, "", "<!-- /section:attacks -->"])


def summary_csv(folder: Path, rows: list[dict[str, str]], profile_key: str, metrics: list[str]) -> None:
    """Write a dashboard-readable summary.csv (profile, metric, mean, std, runs) for an offline result."""
    with (folder / "summary.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["profile", "metric", "mean", "std", "runs"])
        for r in rows:
            for m in metrics:
                if r.get(m) not in (None, ""):
                    w.writerow([r[profile_key], m, r[m], 0, 1])


def main() -> None:
    """Write all sections and charts."""
    for section, tag in ((replay_section(), "replay"), (injection_section(), "injection"),
                         (pruning_section(), "pruning"), (attacks_section(), "attacks")):  # fmt: skip
        write_section(section, tag)
    if (RES / "replay").exists():
        summary_csv(RES / "replay", read(RES / "replay" / "replay_summary.csv"), "profile",
                    ["token_savings", "gateway_ms_p50", "gateway_ms_p95"])  # fmt: skip
    if (RES / "injection").exists():
        summary_csv(RES / "injection", read(RES / "injection" / "comparison.csv"), "detector",
                    ["precision", "recall", "f1", "fpr", "roc_auc"])  # fmt: skip
    if (RES / "attacks").exists():
        summary_csv(RES / "attacks", read(RES / "attacks" / "results.csv"), "config",
                    ["attack_block_rate", "false_positive_rate"])  # fmt: skip
    if (RES / "pruning").exists():
        summary_csv(RES / "pruning", read(RES / "pruning" / "comparison.csv"), "pruner",
                    ["recall_needed", "fields_dropped", "calls_complete"])  # fmt: skip
    print("REPORT.md sections and charts written")


if __name__ == "__main__":
    main()
