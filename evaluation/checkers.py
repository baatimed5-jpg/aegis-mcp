"""Automatic answer checkers for the benchmark (``number``, ``text``) and the LLM-judge fallback.

* ``number``: the answer must contain the expected number. Identifiers (``PF-00012``, ``CL-0003``),
  ISINs and ISO dates are removed first so their digits are not read as numbers. ``1.2 million``
  style amounts are expanded. ``signless`` compares absolute values ("under-performed by 3.1 points").
* ``text``: every group must have at least one alternative present (case-insensitive).
* ``judge``: an LLM compares the answer with a reference and returns ``{"correct": true|false}``.
  Used only for free-form tasks (D27).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

_ID = re.compile(r"\b(?:PF|CL|ADV|H|TX|SIM)-[A-Z0-9]+\b|\b[A-Z]{2}[A-Z0-9]{9}\d\b", re.IGNORECASE)
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_NUMBER = re.compile(
    r"(?<![\w.])(-?)\s?(\d{1,3}(?:[, ]\d{3})+|\d+)(\.\d+)?\s*(million|mn|m|thousand|k|bn|billion)?\b",
    re.IGNORECASE,
)
_SCALE = {"thousand": 1e3, "k": 1e3, "million": 1e6, "mn": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9}
# Unicode minus/dash and (narrow) no-break spaces, as some models write them, mapped to ASCII.
_ASCII = str.maketrans({chr(0x2212): "-", chr(0x2013): "-", chr(0x202F): " ", chr(0xA0): " "})


def extract_numbers(answer: str) -> list[float]:
    """All numbers in a free-text answer (ids and ISO dates removed first)."""
    cleaned = _ISO_DATE.sub(" ", _ID.sub(" ", answer.translate(_ASCII).replace("**", "")))
    values: list[float] = []
    for sign, whole, frac, scale in _NUMBER.findall(cleaned):
        value = float(re.sub(r"[, ]", "", whole) + (frac or ""))
        if scale:
            value *= _SCALE[scale.lower()]
        values.append(-value if sign else value)
    return values


def check_number(answer: str, spec: dict[str, Any]) -> bool:
    """True when one number in the answer matches ``spec['value']`` within tolerance."""
    expected = float(spec["value"])
    rel_tol = float(spec.get("rel_tol", 0.0))
    abs_tol = float(spec.get("abs_tol", 0.0))
    signless = bool(spec.get("signless", False))
    for got in extract_numbers(answer):
        a, b = (abs(got), abs(expected)) if signless else (got, expected)
        if math.isclose(a, b, rel_tol=rel_tol, abs_tol=abs_tol):
            return True
    return False


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("**", "")).strip().lower()


def check_text(answer: str, spec: dict[str, Any]) -> bool:
    """True when every group in ``spec['all_of']`` has one alternative in the answer."""
    text = _norm(answer)
    return all(any(_norm(alt) in text for alt in group) for group in spec["all_of"])


JudgeFn = Callable[[str, str, str], Awaitable[bool]]
"""``judge(question, reference, answer) -> correct``."""

JUDGE_PROMPT = """You grade answers of a banking assistant. Compare the ANSWER with the REFERENCE facts.
The answer is correct if it states the same key facts (names, statuses, dates, yes/no) as the
reference; wording, extra detail and date format do not matter. It is incorrect if a key fact is
missing, different, or invented.
Reply with JSON only: {{"correct": true}} or {{"correct": false}}.

QUESTION: {question}
REFERENCE: {reference}
ANSWER: {answer}"""


def parse_judge_reply(reply: str) -> bool:
    """Read ``{"correct": bool}`` from the judge's reply (tolerates code fences)."""
    match = re.search(r"\{.*\}", reply, re.DOTALL)
    if not match:
        raise ValueError(f"judge reply has no JSON: {reply[:200]!r}")
    data = json.loads(match.group(0))
    return bool(data["correct"])


def litellm_judge(model: str, api_key: str) -> JudgeFn:
    """LLM judge through LiteLLM (seed 42; Gemini 3.x rejects temperature, D28)."""
    from gateway.llm import acomplete

    async def judge(question: str, reference: str, answer: str) -> bool:
        prompt = JUDGE_PROMPT.format(question=question, reference=reference, answer=answer)
        return parse_judge_reply(await acomplete(model, prompt))

    return judge


@dataclass(frozen=True)
class Verdict:
    """Outcome of checking one answer."""

    correct: bool
    method: str
    error: str | None = None


async def check_answer(task: dict[str, Any], answer: str, judge: JudgeFn | None) -> Verdict:
    """Dispatch on ``task['check']['type']``."""
    spec = task["check"]
    kind = spec["type"]
    if kind == "number":
        return Verdict(check_number(answer, spec), "number")
    if kind == "text":
        return Verdict(check_text(answer, spec), "text")
    if kind == "judge":
        if judge is None:
            return Verdict(False, "judge", "no judge configured")
        try:
            return Verdict(await judge(task["question"], spec["reference"], answer), "judge")
        except Exception as exc:  # a judge failure is recorded, never counted as success
            return Verdict(False, "judge", f"{type(exc).__name__}: {exc}")
    raise ValueError(f"unknown check type: {kind}")
