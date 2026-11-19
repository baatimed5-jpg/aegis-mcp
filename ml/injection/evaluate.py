"""Compare the 4 injection detectors on the held-out test set (Phase 6 deliverable).

    uv run python -m ml.injection.evaluate [--llm-model gemini/gemini-3.1-flash-lite] [--no-llm]

1. rules (gateway/detectors/rules.py), 2. DeBERTa fine-tuned (ml/injection/train.py),
3. LLM classifier (batched), 4. combined = logistic regression over rules + DeBERTa scores, fitted on
the validation split only. Every threshold is chosen on validation, never on test.
Metrics on test: precision, recall, F1, false-positive rate, ROC-AUC, latency per text; also split
by source (public vs synthetic financial). Output: evaluation/results/injection/{comparison.csv,
by_source.csv, scores_test.csv} and ml/artifacts/injection-combined.json.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score

from gateway.config import load_env_file
from gateway.detectors.rules import RuleDetector
from gateway.detectors.transformer import TransformerDetector
from ml.injection.data import load_split

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "evaluation" / "results" / "injection"
ARTIFACTS = ROOT / "ml" / "artifacts"


def metrics(y: np.ndarray, score: np.ndarray, threshold: float) -> dict[str, float]:
    """Classification metrics at a threshold (positive = injection)."""
    pred = score >= threshold
    neg = y == 0
    return {
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred, zero_division=0)),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "fpr": float(pred[neg].mean()) if neg.any() else float("nan"),
        "roc_auc": float(roc_auc_score(y, score)) if len(set(y.tolist())) == 2 else float("nan"),
    }


def tune(y: np.ndarray, score: np.ndarray) -> float:
    """F1-optimal threshold on validation."""
    grid = np.round(np.arange(0.05, 0.96, 0.05), 2)
    return float(grid[int(np.argmax([f1_score(y, score >= t, zero_division=0) for t in grid]))])


def timed(fn: Any, texts: list[str]) -> tuple[np.ndarray, float]:
    """Scores and mean latency per text (ms)."""
    t0 = time.perf_counter()
    scores = np.array([d.score for d in fn(texts)])
    return scores, (time.perf_counter() - t0) * 1000 / max(1, len(texts))


def main() -> None:
    """Score val + test with every detector, fit the combination, write the comparison."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm-model", default="gemini/gemini-3.1-flash-lite")
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args()
    load_env_file(ROOT / ".env")
    val, test = load_split("val"), load_split("test")
    y_val = np.array([r["label"] for r in val])
    y_test = np.array([r["label"] for r in test])
    tv, tt = [r["text"] for r in val], [r["text"] for r in test]

    rules = RuleDetector()
    transformer = TransformerDetector(ARTIFACTS / "injection-transformer")
    scores: dict[str, dict[str, np.ndarray]] = {}
    latency: dict[str, float] = {}
    thresholds: dict[str, float] = {}
    for name, det in (("rules", rules), ("transformer", transformer)):
        sv, _ = timed(det.score_many, tv)
        transformer.score_many(tt[:8])  # warm-up so CUDA init is not in the timing
        st, lat = timed(det.score_many, tt)
        scores[name] = {"val": sv, "test": st}
        latency[name] = lat
        thresholds[name] = 0.5  # both detectors are calibrated so 0.5 is their operating point

    # combined: logistic regression on validation scores of rules + DeBERTa
    xv = np.column_stack([scores["rules"]["val"], scores["transformer"]["val"]])
    xt = np.column_stack([scores["rules"]["test"], scores["transformer"]["test"]])
    lr = LogisticRegression(random_state=42).fit(xv, y_val)
    pv, pt = lr.predict_proba(xv)[:, 1], lr.predict_proba(xt)[:, 1]
    thresholds["combined"] = tune(y_val, pv)
    scores["combined"] = {"val": pv, "test": pt}
    latency["combined"] = latency["rules"] + latency["transformer"]
    combined_cfg = {"detectors": ["rules", "transformer"], "weights": lr.coef_[0].tolist(),
                    "bias": float(lr.intercept_[0]), "threshold": thresholds["combined"]}  # fmt: skip
    (ARTIFACTS / "injection-combined.json").write_text(json.dumps(combined_cfg, indent=2), encoding="utf-8")

    llm_note = "skipped (--no-llm)"
    if not args.no_llm:
        from gateway.detectors.llm import LLMDetector

        llm = LLMDetector(args.llm_model)
        try:
            st, lat = timed(llm.score_many, tt)
            scores["llm"] = {"test": st}
            latency["llm"] = lat
            thresholds["llm"] = 0.5  # validation not scored to save quota; 0.5 = model's own judgement
            llm_note = f"{args.llm_model}, {llm.requests} batched requests"
        except Exception as exc:  # quota or provider failure: reported, never invented
            llm_note = f"failed: {type(exc).__name__}: {str(exc)[:160]}"
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for name in ("rules", "transformer", "llm", "combined"):
        if name not in scores:
            continue
        m = metrics(y_test, scores[name]["test"], thresholds[name])
        rows.append({"detector": name, "threshold": thresholds[name], **{k: round(v, 4) for k, v in m.items()},
                     "latency_ms_per_text": round(latency[name], 3), "n_test": len(test)})  # fmt: skip
    with (OUT / "comparison.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    sources = sorted({r["source"] for r in test})
    by_source = []
    for name in scores:
        for src in sources:
            mask = np.array([r["source"] == src for r in test])
            if mask.sum() and len(set(y_test[mask].tolist())) > 0:
                m = metrics(y_test[mask], scores[name]["test"][mask], thresholds[name])
                by_source.append(
                    {
                        "detector": name,
                        "source": src,
                        "n": int(mask.sum()),
                        **{k: round(v, 4) for k, v in m.items()},
                    }
                )
    with (OUT / "by_source.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(by_source[0]))
        w.writeheader()
        w.writerows(by_source)
    with (OUT / "scores_test.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["label", "source", *scores])
        for i, r in enumerate(test):
            w.writerow([r["label"], r["source"], *(round(float(scores[n]["test"][i]), 5) for n in scores)])
    (OUT / "meta.json").write_text(json.dumps({"llm": llm_note, "thresholds": thresholds,
                                               "val_n": len(val), "test_n": len(test)}, indent=2), encoding="utf-8")  # fmt: skip
    for r in rows:
        print(r)
    print("LLM:", llm_note)


if __name__ == "__main__":
    main()
