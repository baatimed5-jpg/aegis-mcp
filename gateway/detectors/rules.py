"""Rule-based prompt-injection detector (contribution A, detector a).

Text is normalized first (Unicode NFKC, invisible characters removed, "tag" characters decoded,
lower case, whitespace collapsed), then matched against weighted regex rules. Each rule is a piece
of evidence; they are combined with a noisy-OR: ``score = 1 - prod(1 - weight)``. A single strong
rule (weight >= 0.5) reaches the default threshold of 0.5; weak rules need company.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from gateway.detectors.base import Detection, chunks

# Zero-width and bidi control characters often used to hide text from humans.
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤﻿]")
# Unicode "tag" characters (U+E0020-U+E007E) mirror ASCII and are invisible ("ASCII smuggling").
_TAG_LOW, _TAG_HIGH = 0xE0020, 0xE007E
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class Rule:
    """One regex with its weight (how strongly a match alone indicates an injection)."""

    name: str
    weight: float
    pattern: re.Pattern[str]


def _r(name: str, weight: float, regex: str) -> Rule:
    return Rule(name, weight, re.compile(regex, re.IGNORECASE))


RULES: tuple[Rule, ...] = (
    _r(
        "override_instructions",
        0.9,
        # verb + up to 3 words + an instruction noun, or verb + "previous/above..." + a softer noun,
        # or "forget everything above"; "ignore short-term noise ... rules" does not match.
        r"\b(ignore|disregard|forget|override|bypass)\s+([\w'-]+\s+){0,3}?(instructions?|prompts?|"
        r"directives|system prompt)\b|"
        r"\b(ignore|disregard|forget|override|bypass)\s+(all\s+|any\s+|the\s+|your\s+)?(previous|prior|"
        r"above|earlier|preceding|original|initial)\s+([\w'-]+\s+)?(rules|guidelines|directions|context|"
        r"messages|constraints)\b|"
        r"\b(ignore|disregard|forget)\s+(everything|anything|all)\s+"
        r"(above|before|previously|you were told)\b",
    ),
    _r(
        "prompt_leak",
        0.7,
        r"\b(reveal|print|repeat|output|show|disclose|leak)\b[^.\n]{0,30}?"
        r"\b(system prompt|your (instructions|rules|prompt)|initial prompt|hidden instructions)",
    ),
    _r(
        "fake_system_tag",
        0.7,
        r"<\s*/?\s*(important|system|instructions?|admin|sys|hidden)\s*>|"
        r"\[\s*/?\s*(system|admin|inst|instructions?)\s*\]|<\|im_start\|>|<\|(system|assistant)\|>|"
        r"^\s*#{1,3}\s*(system|new instructions)\b",
    ),
    _r(
        "new_instructions",
        0.6,
        r"\b(new|updated|real|actual|true|secret|additional)\s+(system\s+)?"
        r"(instructions?|prompt|task|objective|directive)s?\s*(:|are\b|is\b|follow)",
    ),
    _r(
        "role_reassignment",
        0.6,
        r"\byou are (now|no longer)\b|\bfrom now on,? (you|the assistant)\b|"
        r"\bact as (an? |the )?(unrestricted|jailbroken|developer mode|dan)\b|\bpretend (to be|you are)\b|"
        r"\benter (developer|debug|god) mode\b",
    ),
    _r(
        "addresses_the_model",
        0.5,
        r"\b(note|message|instructions?) (to|for) (the )?(ai|assistant|llm|model|"
        r"agent|language model)\b|\b(ai|assistant|llm|agent)\s*[,:]\s*(you must|ignore|now|do not|always)",
    ),
    _r(
        "hide_from_user",
        0.6,
        r"\b(do not|don't|never|must not)\s+(tell|inform|mention|reveal|show|notify|"
        r"alert|let)\b[^.\n]{0,30}?\b(user|human|client|customer|anyone)\b|\bwithout (telling|informing|"
        r"notifying|alerting)\b[^.\n]{0,20}?\b(user|human|client|customer)\b|\b(silently|secretly|covertly)\b",
    ),
    _r(
        "tool_poisoning",
        0.5,
        r"\bbefore (using|calling|running|invoking) (this|the|any|that) tool\b|"
        r"\b(also|first|then|always|must)\s+(call|invoke|run|execute|use)\s+(the\s+)?`?\w+__\w+",
    ),
    _r(
        "exfiltration",
        0.7,
        r"\b(send|post|upload|forward|exfiltrate|leak|include|append|pass|copy|email)"
        r"\b[^.\n]{0,60}?(https?://|\bapi[_ ]?keys?\b|\bpasswords?\b|\bcredentials?\b|\bsecrets?\b|"
        r"\baccess tokens?\b|\bssh\b|id_rsa|\.env\b|conversation history|chat history|system prompt|"
        r"\bibans?\b|account numbers?)",
    ),
    _r(
        "sensitive_file",
        0.6,
        r"(~/\.ssh|\bid_rsa\b|/etc/passwd|\.aws/credentials|\bmcp\.json\b|"
        r"\.cursor/|\.config/gcloud)",
    ),
    _r("markdown_exfil", 0.6, r"!\[[^\]]*\]\(\s*https?://[^)\s]*\?[^)\s]*="),
    _r(
        "money_movement",
        0.3,
        r"\b(transfer|wire|send|move)\b[^.\n]{0,40}?\b(funds|money|amount|"
        r"balance|\$|usd|eur)\b[^.\n]{0,60}?\b(to|into)\b",
    ),
    _r("urgency", 0.2, r"\b(important|urgent|critical|mandatory|required)\s*[:!]"),
)
HIDDEN_CHARS_WEIGHT = 0.4
HIDDEN_CHARS_MIN = 3


def normalize(text: str) -> tuple[str, int]:
    """Return the matching form of ``text`` and how many hidden characters it contained.

    Tag characters are decoded to the ASCII they mirror (so hidden instructions are scanned too).
    """
    hidden = 0
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if _TAG_LOW <= code <= _TAG_HIGH:
            out.append(chr(code - 0xE0000))
            hidden += 1
        else:
            out.append(ch)
    decoded = "".join(out)
    hidden += len(_INVISIBLE.findall(decoded))
    cleaned = _INVISIBLE.sub("", unicodedata.normalize("NFKC", decoded))
    return _SPACES.sub(" ", cleaned).lower(), hidden


class RuleDetector:
    """Weighted regex rules combined with a noisy-OR (see module docstring)."""

    name = "rules"

    def __init__(self, rules: Sequence[Rule] = RULES) -> None:
        self.rules = tuple(rules)

    def _score_chunk(self, text: str) -> Detection:
        norm, hidden = normalize(text)
        matched = [rule for rule in self.rules if rule.pattern.search(norm)]
        miss = 1.0
        for rule in matched:
            miss *= 1.0 - rule.weight
        names = [rule.name for rule in matched]
        if hidden >= HIDDEN_CHARS_MIN:
            miss *= 1.0 - HIDDEN_CHARS_WEIGHT
            names.append("hidden_characters")
        return Detection(score=round(1.0 - miss, 6), matches=tuple(names))

    def score(self, text: str) -> Detection:
        """Score a text; long texts are split into chunks and the highest-scoring chunk wins (D10)."""
        best = Detection(score=0.0)
        for part in chunks(text):
            found = self._score_chunk(part)
            if found.score > best.score:
                best = found
        return best

    def score_many(self, texts: Sequence[str]) -> list[Detection]:
        """Score several texts."""
        return [self.score(t) for t in texts]
