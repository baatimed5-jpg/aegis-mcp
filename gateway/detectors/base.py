"""Common interface of the prompt-injection detectors (rules now; DeBERTa, LLM and combined in Phase 6)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

CHUNK_CHARS = 2000
CHUNK_OVERLAP = 200


@dataclass(frozen=True)
class Detection:
    """Result for one text: ``score`` in [0, 1] (1 = surely an injection) and what triggered it."""

    score: float
    matches: tuple[str, ...] = field(default=())


class InjectionDetector(Protocol):
    """Scores texts for prompt injection."""

    name: str

    def score(self, text: str) -> Detection:
        """Score one text."""
        ...

    def score_many(self, texts: Sequence[str]) -> list[Detection]:
        """Score several texts (detectors with batching override this)."""
        ...


def chunks(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split long texts into overlapping windows (D10: the text's score is the max over chunks)."""
    if len(text) <= size:
        return [text]
    step = size - overlap
    return [text[i : i + size] for i in range(0, len(text) - overlap, step)]
