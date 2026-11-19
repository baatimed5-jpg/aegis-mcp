"""Fine-tuned transformer injection detector (DeBERTa-v3-small, ``ml/injection/train.py``).

Long texts are chunked (D10) and the text's score is the max over chunks. The model runs on CUDA
when available, otherwise CPU. Scores are calibrated so that the trained decision threshold maps
to 0.5, which keeps the gateway's ``threshold: 0.5`` meaning "the model's own operating point".
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from gateway.detectors.base import Detection, chunks


def calibrate(p: float, threshold: float) -> float:
    """Piecewise-linear map of a probability so that ``threshold`` becomes 0.5."""
    if p < threshold:
        return 0.5 * p / threshold if threshold > 0 else 0.0
    return 0.5 + 0.5 * (p - threshold) / (1 - threshold) if threshold < 1 else 1.0


class TransformerDetector:
    """Sequence classifier loaded from a local folder."""

    name = "transformer"

    def __init__(self, path: Path | str, device: str | None = None, batch_size: int = 32) -> None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._torch = torch
        self.path = Path(path)
        meta: dict[str, Any] = json.loads((self.path / "aegis_meta.json").read_text(encoding="utf-8"))
        self.threshold = float(meta.get("threshold", 0.5))
        self.max_len = int(meta.get("max_len", 256))
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.tokenizer = AutoTokenizer.from_pretrained(self.path)
        self.model = AutoModelForSequenceClassification.from_pretrained(self.path).to(self.device).eval()
        self.batch_size = batch_size
        self.last_latency_ms = 0.0

    def _probs(self, texts: list[str]) -> list[float]:
        torch = self._torch
        out: list[float] = []
        with torch.no_grad():
            for i in range(0, len(texts), self.batch_size):
                enc = self.tokenizer(
                    texts[i : i + self.batch_size], truncation=True, max_length=self.max_len,
                    padding=True, return_tensors="pt",
                )  # fmt: skip
                enc = {k: v.to(self.device) for k, v in enc.items()}
                logits = self.model(**enc).logits.float()
                out += torch.softmax(logits, dim=-1)[:, 1].tolist()
        return out

    def score_many(self, texts: Sequence[str]) -> list[Detection]:
        """Score several texts (all chunks in one batched pass)."""
        t0 = time.perf_counter()
        pieces: list[str] = []
        owner: list[int] = []
        for i, text in enumerate(texts):
            for chunk in chunks(text):
                pieces.append(chunk)
                owner.append(i)
        best = [0.0] * len(texts)
        for i, p in zip(owner, self._probs(pieces) if pieces else [], strict=True):
            best[i] = max(best[i], p)
        self.last_latency_ms = (time.perf_counter() - t0) * 1000
        return [Detection(score=calibrate(p, self.threshold)) for p in best]

    def score(self, text: str) -> Detection:
        """Score one text."""
        return self.score_many([text])[0]
