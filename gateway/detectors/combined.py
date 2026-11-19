"""Combined detector: logistic regression over the scores of several detectors.

Weights are fitted on the validation split (``ml/injection/evaluate.py``) and saved as JSON:
``{"detectors": ["rules", "transformer"], "weights": [...], "bias": b, "threshold": t}``. The output is
calibrated like the transformer detector (the fitted threshold maps to 0.5).
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from gateway.detectors.base import Detection, InjectionDetector
from gateway.detectors.transformer import calibrate


class CombinedDetector:
    """sigmoid(sum_i w_i * score_i + b) over member detectors."""

    name = "combined"

    def __init__(
        self, members: list[InjectionDetector], weights: list[float], bias: float, threshold: float = 0.5
    ) -> None:
        if len(members) != len(weights):
            raise ValueError("one weight per member detector")
        self.members = members
        self.weights = weights
        self.bias = bias
        self.threshold = threshold

    @classmethod
    def from_file(cls, path: Path | str, members: dict[str, InjectionDetector]) -> CombinedDetector:
        """Load fitted weights; ``members`` maps detector names to instances."""
        cfg: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            [members[n] for n in cfg["detectors"]], cfg["weights"], cfg["bias"], cfg.get("threshold", 0.5)
        )

    def probability(self, member_scores: Sequence[float]) -> float:
        """Raw combined probability from member scores."""
        z = self.bias + sum(w * s for w, s in zip(self.weights, member_scores, strict=True))
        return 1 / (1 + math.exp(-z))

    def score_many(self, texts: Sequence[str]) -> list[Detection]:
        """Score with every member, then combine."""
        per_member = [m.score_many(texts) for m in self.members]
        out = []
        for i in range(len(texts)):
            p = self.probability([d[i].score for d in per_member])
            matches = tuple(m for d in per_member for m in d[i].matches)
            out.append(Detection(score=calibrate(p, self.threshold), matches=matches))
        return out

    def score(self, text: str) -> Detection:
        """Score one text."""
        return self.score_many([text])[0]
