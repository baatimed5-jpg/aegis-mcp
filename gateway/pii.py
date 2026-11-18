"""PII detection and masking with Presidio (spaCy ``en_core_web_sm``, D23).

Values are analysed one JSON string at a time; the field name is passed to Presidio as context
(e.g. a value under ``phone`` gets a higher PHONE_NUMBER score). Only findings with
``score >= min_score`` are masked, as ``<ENTITY_TYPE>``.

Speed (D23): when every configured entity is pattern-based, spaCy's statistical pipes are switched
off (only the tokenizer runs) and strings without a digit or ``@`` are skipped, since none of those
entities can occur in them. NER entities (e.g. PERSON) turn both shortcuts off.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from presidio_analyzer import AnalyzerEngine, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider

from gateway.pipeline.content import Path, last_key, map_strings

DEFAULT_ENTITIES = ("IBAN_CODE", "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "US_SSN")
# Entities Presidio finds with regex/checksum recognizers only (no NER model needed).
PATTERN_ENTITIES = frozenset(
    {
        "IBAN_CODE", "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "US_SSN", "IP_ADDRESS",
        "US_BANK_NUMBER", "US_PASSPORT", "US_DRIVER_LICENSE", "US_ITIN", "CRYPTO", "UK_NHS",
    }
)  # fmt: skip
_MAY_HOLD_PATTERN_PII = re.compile(r"[0-9@]")
_KEY_SPLIT = re.compile(r"[_\W]+")
_RESULT_ROOTS = frozenset({"structured", "content"})


class PiiMasker:
    """Finds and masks PII in strings and nested JSON-like objects (thread-safe for reads)."""

    def __init__(
        self,
        entities: Sequence[str] = DEFAULT_ENTITIES,
        min_score: float = 0.5,
        model: str = "en_core_web_sm",
    ) -> None:
        self.entities = list(entities)
        self.min_score = min_score
        self.pattern_only = all(e in PATTERN_ENTITIES for e in self.entities)
        nlp_engine = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": model}],
            }
        ).create_engine()
        if self.pattern_only:
            nlp = nlp_engine.nlp["en"]  # type: ignore[attr-defined]
            nlp.select_pipes(disable=list(nlp.pipe_names))
        self.analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])
        self.analyzer.analyze("warm up", language="en")

    def find(self, text: str, context: list[str] | None = None) -> list[RecognizerResult]:
        """PII findings in ``text`` above ``min_score`` (overlaps resolved: highest score wins)."""
        if self.pattern_only and not _MAY_HOLD_PATTERN_PII.search(text):
            return []
        results = self.analyzer.analyze(text, language="en", entities=self.entities, context=context)
        kept: list[RecognizerResult] = []
        for r in sorted(results, key=lambda r: (-r.score, r.start)):
            if r.score >= self.min_score and all(r.end <= k.start or r.start >= k.end for k in kept):
                kept.append(r)
        return sorted(kept, key=lambda r: r.start)

    def mask_text(self, text: str, context: list[str] | None = None) -> tuple[str, int]:
        """Replace each finding by ``<ENTITY_TYPE>``; return the new text and the number of findings."""
        findings = self.find(text, context)
        for r in reversed(findings):
            text = f"{text[: r.start]}<{r.entity_type}>{text[r.end :]}"
        return text, len(findings)

    def mask_value(self, value: Any) -> tuple[Any, int]:
        """Mask every string inside a nested object, using each field name as context."""
        total = 0

        def mask(path: Path, text: str) -> str:
            nonlocal total
            masked, count = self.mask_text(text, key_context(path))
            total += count
            return masked

        return map_strings(value, mask), total


def key_context(path: Path) -> list[str] | None:
    """Words of the nearest field name (``"tax_id"`` -> ``["tax", "id"]``) for Presidio's context."""
    if path and path[0] in _RESULT_ROOTS:  # skip the "structured"/"content" marker of result paths
        path = path[1:]
    key = last_key(path)
    if not key:
        return None
    words = [w.lower() for w in _KEY_SPLIT.split(key) if w]
    return words or None
