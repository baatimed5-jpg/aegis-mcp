"""Features of one (question, tool, field path) for the learned pruner (shared by training and runtime).

* semantic similarity between the question and the field name (sentence-transformers, cosine);
* lexical overlap between question words and field-name words;
* tool, field kind (number/string/...), list membership, value size;
* a learned per-field prior through the field id (categorical).
"""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from typing import Any

import numpy as np

from gateway.optimizer.fields import humanize, value_kind

ENCODER = "sentence-transformers/all-MiniLM-L6-v2"
TOOLS = [
    "get_client",
    "list_portfolios",
    "get_holdings",
    "get_transactions",
    "get_portfolio_performance",
    "transfer_funds",
]
KINDS = ["number", "string", "bool", "list", "object", "null"]
_WORD = re.compile(r"[a-z]+")
SYNONYMS = {
    "position": "holding",
    "positions": "holdings",
    "sale": "sell",
    "cash": "cash",
    "advisor": "advisor",
}


@lru_cache(maxsize=1)
def encoder() -> Any:
    """The sentence encoder (loaded once, CPU)."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(ENCODER, device="cpu")


@lru_cache(maxsize=4096)
def _embed_one(text: str) -> tuple[float, ...]:
    vec = encoder().encode([text], normalize_embeddings=True)[0]
    return tuple(float(x) for x in vec)


def embed(texts: list[str]) -> np.ndarray:
    """Normalized embeddings (cached per text)."""
    return np.array([_embed_one(t) for t in texts])


def _words(text: str) -> set[str]:
    words = {w for w in _WORD.findall(text.lower()) if len(w) > 2}
    return words | {SYNONYMS[w] for w in words if w in SYNONYMS}


def lexical_overlap(question: str, path: str) -> float:
    """Share of field-name words whose 4-letter stem appears in the question."""
    fw = _words(humanize(path))
    if not fw:
        return 0.0
    qstems = {w[:4] for w in _words(question)}
    return sum(w[:4] in qstems for w in fw) / len(fw)


FEATURE_NAMES = ["similarity", "lexical", "is_list", "depth", "log_len", "field_id",
                 *[f"tool_{t}" for t in TOOLS], *[f"kind_{k}" for k in KINDS]]  # fmt: skip


def build_features(question: str, tool: str, paths: dict[str, Any], vocab: dict[str, int]) -> np.ndarray:
    """One row per candidate path, columns = ``FEATURE_NAMES``."""
    names = list(paths)
    if not names:
        return np.zeros((0, len(FEATURE_NAMES)))
    q = embed([question])[0]
    sims = embed([humanize(p) for p in names]) @ q
    rows = []
    for path, sim in zip(names, sims, strict=True):
        value = paths[path]
        rows.append([
            float(sim), lexical_overlap(question, path), float("[]" in path), float(path.count(".")),
            math.log1p(len(json.dumps(value, default=str))), float(vocab.get(f"{tool}:{path}", -1)),
            *[float(tool == t) for t in TOOLS], *[float(value_kind(value) == k) for k in KINDS],
        ])  # fmt: skip
    return np.array(rows)
