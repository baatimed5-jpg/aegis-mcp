"""Train the learned pruner and compare it with the rule pruner (Phase 7).

    uv run python -m ml.pruning.train

Data: ``ml/data/pruning/labels.jsonl`` (LLM labels, one row per reference call). Each candidate field
of each call is one example (label = field needed). Split BY TASK (seed 42): 30 tasks train,
10 validation, 10 test, so test questions are never seen in training.
Models: logistic regression vs LightGBM on the same features (gateway/optimizer/features.py).
Threshold: recall-first, the highest threshold that keeps >= 95 % of needed fields on validation;
the kept model is the one that drops the most fields on validation at that recall
(dropping a needed field can cost a wrong answer; keeping a useless one only costs tokens).
Output: ml/artifacts/pruning_model.joblib (selected model) and
evaluation/results/pruning/{comparison.csv, meta.json}.
"""

from __future__ import annotations

import csv
import json
import random
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from lightgbm import LGBMClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from evaluation.oracle import dataset
from gateway.optimizer.features import ENCODER, FEATURE_NAMES, build_features
from gateway.optimizer.rules import load_rules

ROOT = Path(__file__).resolve().parents[2]
LABELS = ROOT / "ml" / "data" / "pruning" / "labels.jsonl"
MODEL_OUT = ROOT / "ml" / "artifacts" / "pruning_model.joblib"
RESULTS = ROOT / "evaluation" / "results" / "pruning"
SEED = 42
TARGET_RECALL = 0.95


def load_rows() -> list[dict[str, Any]]:
    """Labeled calls."""
    return [json.loads(line) for line in LABELS.read_text(encoding="utf-8").splitlines() if line]


def example_values(row: dict[str, Any]) -> dict[str, Any]:
    """Candidate paths with an example value, recomputed from the real tool result."""
    import asyncio

    from gateway.optimizer.fields import candidate_paths
    from ml.pruning.label import tool_result

    _task_id, tool, args = row["key"].split("|", 2)
    data = asyncio.run(tool_result(tool, json.loads(args)))
    return candidate_paths(data or {})


def matrix(
    rows: list[dict[str, Any]], vocab: dict[str, int]
) -> tuple[np.ndarray, np.ndarray, list[tuple[int, str]]]:
    """Features, labels and (row index, path) ids."""
    xs, ys, ids = [], [], []
    for i, r in enumerate(rows):
        paths = r["_values"]
        x = build_features(r["question"], r["tool"], paths, vocab)
        needed = set(r["needed_llm"])
        xs.append(x)
        ys += [int(p in needed) for p in paths]
        ids += [(i, p) for p in paths]
    return np.vstack(xs), np.array(ys), ids


def recall_first_threshold(y: np.ndarray, p: np.ndarray, target: float = TARGET_RECALL) -> float:
    """Highest threshold whose recall of needed fields stays >= target (candidates = observed scores)."""
    best = 0.0
    for t in np.unique(p):
        kept = p >= t
        recall = (kept & (y == 1)).sum() / max(1, (y == 1).sum())
        if recall >= target:
            best = float(t)
    return best


def field_metrics(y: np.ndarray, keep: np.ndarray, ids: list[tuple[int, str]]) -> dict[str, float]:
    """Field-level recall/precision of KEEP decisions, drop rate and call-level completeness."""
    needed = y == 1
    by_call: dict[int, bool] = {}
    for (call, _p), n, k in zip(ids, needed, keep, strict=True):
        by_call[call] = by_call.get(call, True) and (k or not n)
    return {
        "recall_needed": float((keep & needed).sum() / max(1, needed.sum())),
        "precision_kept": float((keep & needed).sum() / max(1, keep.sum())),
        "fields_dropped": float(1 - keep.mean()),
        "calls_complete": float(np.mean(list(by_call.values()))),
    }


def rule_keep(rows: list[dict[str, Any]], ids: list[tuple[int, str]]) -> np.ndarray:
    """Keep decisions of the Phase 5 rules on the same candidate paths."""
    rules = load_rules(ROOT / "config" / "pruning_rules.yaml")

    def dropped(tool: str, path: str) -> bool:
        return any(path == r or path.startswith(r + ".") for r in rules.for_tool(tool))

    return np.array([not dropped(rows[i]["tool"], p) for i, p in ids])


def main() -> None:
    """Split, train both models, choose thresholds on validation, report on test, save the best."""
    dataset()  # warm the seeded dataset once
    rows = load_rows()
    for r in rows:
        r["_values"] = example_values(r)
    tasks = sorted({r["task_id"] for r in rows})
    rng = random.Random(SEED)
    rng.shuffle(tasks)
    n = len(tasks)
    split = {t: "train" for t in tasks[: int(n * 0.6)]} | {
        t: "val" for t in tasks[int(n * 0.6) : int(n * 0.8)]
    }
    split |= {t: "test" for t in tasks[int(n * 0.8) :]}
    parts = {s: [r for r in rows if split[r["task_id"]] == s] for s in ("train", "val", "test")}
    vocab: dict[str, int] = {}
    for r in parts["train"]:
        for p in r["_values"]:
            vocab.setdefault(f"{r['tool']}:{p}", len(vocab))
    data = {s: matrix(parts[s], vocab) for s in parts}
    models = {
        "logreg": make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, class_weight="balanced", random_state=SEED)),
        "lightgbm": LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=15, min_child_samples=5,
                                   class_weight="balanced", random_state=SEED, verbose=-1),
    }  # fmt: skip
    results = []
    fitted: dict[str, tuple[Any, float, float]] = {}
    xt, yt, idt = data["test"]
    for name, model in models.items():
        x, y, _ = data["train"]
        model.fit(x, y)
        xv, yv, _ = data["val"]
        pv = model.predict_proba(xv)[:, 1]
        threshold = recall_first_threshold(yv, pv)
        ap_val = float(average_precision_score(yv, pv))
        pt = model.predict_proba(xt)[:, 1]
        m = field_metrics(yt, pt >= threshold, idt)
        results.append({"pruner": f"learned_{name}", "threshold": threshold, "val_pr_auc": round(ap_val, 4),
                        "test_pr_auc": round(float(average_precision_score(yt, pt)), 4),
                        **{k: round(v, 4) for k, v in m.items()}})  # fmt: skip
        drop_val = float((pv < threshold).mean())  # selection: fields dropped on val at the target recall
        results[-1]["val_fields_dropped"] = round(drop_val, 4)
        fitted[name] = (model, threshold, drop_val)
    rm = field_metrics(yt, rule_keep(parts["test"], idt), idt)
    results.append({"pruner": "rules", "threshold": "", "val_pr_auc": "", "test_pr_auc": "", "val_fields_dropped": "",
                    **{k: round(v, 4) for k, v in rm.items()}})  # fmt: skip
    keep_all = field_metrics(yt, np.ones_like(yt, dtype=bool), idt)
    results.append({"pruner": "none", "threshold": "", "val_pr_auc": "", "test_pr_auc": "", "val_fields_dropped": "",
                    **{k: round(v, 4) for k, v in keep_all.items()}})  # fmt: skip
    best = max(fitted, key=lambda k: fitted[k][2])  # most fields dropped on val while recall >= target
    model, threshold, _ = fitted[best]
    MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "threshold": threshold, "vocab": vocab, "tools": sorted({r["tool"] for r in rows}),
                 "features": FEATURE_NAMES, "encoder": ENCODER, "kind": best}, MODEL_OUT)  # fmt: skip
    RESULTS.mkdir(parents=True, exist_ok=True)
    with (RESULTS / "comparison.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0]))
        w.writeheader()
        w.writerows(results)
    agree = [len(set(r["needed_llm"]) & set(r["needed_heuristic"])) / max(1, len(r["needed_heuristic"]))
             for r in rows if r["needed_heuristic"]]  # fmt: skip
    meta = {"selected": best, "threshold": threshold, "target_recall": TARGET_RECALL,
            "examples": {s: len(data[s][1]) for s in data}, "positives": {s: int(data[s][1].sum()) for s in data},
            "tasks": {s: len({r['task_id'] for r in parts[s]}) for s in parts},
            "llm_vs_heuristic_coverage": round(float(np.mean(agree)), 4) if agree else None,
            "labeler": rows[0]["labeler"] if rows else None}  # fmt: skip
    (RESULTS / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    for r in results:
        print(r)
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
