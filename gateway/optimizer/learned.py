"""Learned field pruning (Phase 7): drop fields a trained model predicts the agent does not need.

The bundle (``ml/artifacts/pruning_model.joblib``, built by ``ml/pruning/train.py``) holds the model,
the recall-first threshold chosen on validation and the field vocabulary. The question comes from
``_meta["aegis/question"]`` (D4); without it, or for a tool the model never saw, the pruner falls
back to the rule pruner, so behaviour degrades to Phase 5, never to "drop everything".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib

from gateway.optimizer.features import build_features
from gateway.optimizer.fields import candidate_paths
from gateway.optimizer.rules import PruningRules, load_rules
from gateway.pipeline.context import CallContext

QUESTION_KEY = "aegis/question"
ROOT = Path(__file__).resolve().parents[2]


class LearnedPruner:
    """Keeps a field when P(needed | question, tool, field) >= threshold."""

    def __init__(
        self, bundle: dict[str, Any], fallback: PruningRules, threshold: float | None = None
    ) -> None:
        self.model = bundle["model"]
        self.vocab: dict[str, int] = bundle["vocab"]
        self.tools: set[str] = set(bundle["tools"])
        self.threshold = float(threshold if threshold is not None else bundle["threshold"])
        self.fallback = fallback
        self.fallbacks = 0

    @classmethod
    def load(
        cls, path: Path, threshold: float | None = None, rules_file: Path | None = None
    ) -> LearnedPruner:
        """Load a trained bundle (raises if it was not trained yet)."""
        if not path.exists():
            raise FileNotFoundError(f"learned pruning model not found: {path} (run ml.pruning.train)")
        rules = load_rules(rules_file or ROOT / "config" / "pruning_rules.yaml")
        return cls(joblib.load(path), rules, threshold)

    def keep_probabilities(self, question: str, tool: str, data: dict[str, Any]) -> dict[str, float]:
        """P(needed) for every candidate path of a result."""
        paths = candidate_paths(data)
        x = build_features(question, tool, paths, self.vocab)
        if len(x) == 0:
            return {}
        probs = self.model.predict_proba(x)[:, 1]
        return {p: float(v) for p, v in zip(paths, probs, strict=True)}

    async def drop_paths(self, ctx: CallContext, data: dict[str, Any]) -> list[str]:
        """Paths predicted unnecessary (rule paths when the question or tool is unknown)."""
        question = ctx.meta.get(QUESTION_KEY)
        if not isinstance(question, str) or not question or ctx.tool not in self.tools:
            self.fallbacks += 1
            return self.fallback.for_tool(ctx.tool)
        import anyio

        probs = await anyio.to_thread.run_sync(self.keep_probabilities, question, ctx.tool, data)
        return [p for p, v in probs.items() if v < self.threshold]
