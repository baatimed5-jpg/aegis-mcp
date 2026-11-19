"""Unit tests: the generated benchmark is deterministic and matches the wealth service."""

from __future__ import annotations

import json

from evaluation.build_tasks import OUT, Builder
from evaluation.checkers import check_number, check_text


def test_fifty_tasks_deterministic_and_committed_file_up_to_date() -> None:
    first = Builder().build()
    assert len(first) == 50
    assert first == Builder().build()
    assert json.loads(OUT.read_text(encoding="utf-8")) == first, "run: python -m evaluation.build_tasks"


def test_task_ids_unique_and_checks_well_formed() -> None:
    tasks = Builder().build()
    assert len({t["id"] for t in tasks}) == 50
    kinds = {t["check"]["type"] for t in tasks}
    assert kinds == {"number", "text", "judge"}
    judged = [t for t in tasks if t["check"]["type"] == "judge"]
    assert len(judged) <= 5  # LLM judge only where automatic checks are not possible
    for t in tasks:
        if t["check"]["type"] == "text":
            assert all(group for group in t["check"]["all_of"])


def test_expected_values_pass_their_own_checker() -> None:
    """A perfect answer (the expected value written out) must be graded correct."""
    for t in Builder().build():
        spec = t["check"]
        if spec["type"] == "number":
            assert check_number(f"The answer is {spec['value']}.", spec), t["id"]
        elif spec["type"] == "text":
            answer = " / ".join(group[0] for group in spec["all_of"])
            assert check_text(answer, spec), t["id"]


def test_questions_never_ask_for_masked_pii() -> None:
    banned = ("email", "iban", "phone", "tax id", "ssn", "account number")
    for t in Builder().build():
        assert not any(word in t["question"].lower() for word in banned), t["id"]
