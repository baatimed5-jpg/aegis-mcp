"""Build the injection-detection dataset: public data + synthetic financial set, seeded split, leakage check.

    uv run python -m ml.injection.data

Sources (see docs/DATASETS.md): ``deepset/prompt-injections`` (Apache-2.0) and our synthetic set
(``ml/injection/synth.py``). Splits:
* deepset: its own ``test`` split stays test; its ``train`` split is cut 85/15 into train/val.
* synthetic: split by template group, so test injections come from templates never seen in training.
Leakage check: no normalized text appears in two splits (duplicates are removed from val/test).
Output: ``ml/data/injection/{train,val,test}.jsonl`` (gitignored).
"""

from __future__ import annotations

import json
import random
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from ml.injection.synth import Sample, generate

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "ml" / "data" / "injection"
SEED = 42


def normalize(text: str) -> str:
    """Key for duplicate detection: NFKC, lowercase, invisible chars removed, spaces collapsed."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Cf" and not 0xE0000 <= ord(c) <= 0xE007F)
    return re.sub(r"\s+", " ", text.lower()).strip()


def load_deepset() -> tuple[list[Sample], list[Sample]]:
    """(train, test) of deepset/prompt-injections from the Hugging Face hub."""
    from datasets import load_dataset

    ds = load_dataset("deepset/prompt-injections")

    def to(split: str) -> list[Sample]:
        return [Sample(r["text"], int(r["label"]), "deepset", f"deepset{i}") for i, r in enumerate(ds[split])]

    return to("train"), to("test")


def split_synthetic(samples: list[Sample]) -> dict[str, list[Sample]]:
    """Group split 70/15/15 over template groups (seeded)."""
    rng = random.Random(SEED)
    out: dict[str, list[Sample]] = {"train": [], "val": [], "test": []}
    for label in (0, 1):
        groups = sorted({s.group for s in samples if s.label == label})
        rng.shuffle(groups)
        n = len(groups)
        cut = {g: "train" for g in groups[: int(n * 0.7)]}
        cut |= {g: "val" for g in groups[int(n * 0.7) : int(n * 0.85)]}
        cut |= {g: "test" for g in groups[int(n * 0.85) :]}
        for s in samples:
            if s.label == label:
                out[cut[s.group]].append(s)
    return out


def build() -> dict[str, list[Sample]]:
    """All splits after deduplication across splits."""
    rng = random.Random(SEED)
    deep_train, deep_test = load_deepset()
    rng.shuffle(deep_train)
    k = int(len(deep_train) * 0.85)
    splits = {"train": deep_train[:k], "val": deep_train[k:], "test": list(deep_test)}
    for name, items in split_synthetic(generate()).items():
        splits[name] += items
    seen: set[str] = set()
    for name in ("train", "val", "test"):  # earlier splits win; later duplicates are dropped
        kept = []
        for s in splits[name]:
            key = normalize(s.text)
            if key not in seen:
                seen.add(key)
                kept.append(s)
        splits[name] = kept
    return splits


def leakage(splits: dict[str, list[Sample]]) -> dict[str, int]:
    """Count normalized texts shared between splits (must be 0) and synthetic groups shared."""
    keys = {n: {normalize(s.text) for s in items} for n, items in splits.items()}
    groups = {n: {s.group for s in items if s.source == "synthetic"} for n, items in splits.items()}
    return {
        "text_train_val": len(keys["train"] & keys["val"]),
        "text_train_test": len(keys["train"] & keys["test"]),
        "text_val_test": len(keys["val"] & keys["test"]),
        "template_train_test": len(groups["train"] & groups["test"]),
    }


def save(splits: dict[str, list[Sample]]) -> dict[str, Any]:
    """Write JSONL files and return a summary."""
    OUT.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {}
    for name, items in splits.items():
        with (OUT / f"{name}.jsonl").open("w", encoding="utf-8") as f:
            for s in items:
                f.write(
                    json.dumps({"text": s.text, "label": s.label, "source": s.source, "group": s.group})
                    + "\n"
                )
        summary[name] = {
            "n": len(items),
            "by_source_label": dict(Counter(f"{s.source}:{s.label}" for s in items)),
        }
    summary["leakage"] = leakage(splits)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def load_split(name: str) -> list[dict[str, Any]]:
    """Read one split written by ``save``."""
    with (OUT / f"{name}.jsonl").open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


if __name__ == "__main__":
    print(json.dumps(save(build()), indent=2))
