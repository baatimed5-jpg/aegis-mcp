"""Unit tests: benchmark answer checkers (number, text, judge dispatch)."""

from __future__ import annotations

import pytest

from evaluation.checkers import (
    check_answer,
    check_number,
    check_text,
    extract_numbers,
    parse_judge_reply,
)


def test_extract_numbers_ignores_ids_and_dates() -> None:
    text = "Portfolio PF-00045 (client CL-0003, ISIN CHBKK4CIJ2V4) received 4 dividends since 2026-01-01."
    assert extract_numbers(text) == [4.0]


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("The total is $457,140.95.", 457140.95),
        ("About 1.2 million GBP", 1_200_000.0),
        ("Return: -6.75%", -6.75),
        ("Return: " + chr(0x2212) + "6.75 %", -6.75),
        ("Cash: 925" + chr(0x202F) + "750.88 EUR", 925750.88),
        ("**73,389.89 EUR** would remain", 73389.89),
    ],
)
def test_extract_numbers_formats(answer: str, expected: float) -> None:
    assert expected in extract_numbers(answer)


def test_number_tolerances() -> None:
    money = {"type": "number", "value": 457140.95, "rel_tol": 0.005}
    assert check_number("roughly 457,000", money)
    assert not check_number("roughly 450,000", money)
    pct = {"type": "number", "value": -6.75, "abs_tol": 0.051}
    assert check_number("-6.8%", pct)
    assert not check_number("6.75%", pct)  # sign matters by default
    assert check_number("underperformed by 6.75 points", {**pct, "signless": True})


def test_number_not_fooled_by_month_word() -> None:
    assert extract_numbers("over 12 months") == [12.0]


def test_check_text_groups() -> None:
    spec = {"type": "text", "all_of": [["Zachary Rice"], ["New York", "NYC"]]}
    assert check_text("Their advisor is **Zachary  Rice** (New York office).", spec)
    assert not check_text("Their advisor is Zachary Rice.", spec)


def test_parse_judge_reply() -> None:
    assert parse_judge_reply('```json\n{"correct": true}\n```') is True
    assert parse_judge_reply('{"correct": false}') is False
    with pytest.raises(ValueError, match="no JSON"):
        parse_judge_reply("yes")


async def test_check_answer_dispatch_and_judge_failure() -> None:
    task = {"question": "q", "check": {"type": "judge", "reference": "r"}}

    async def good(q: str, r: str, a: str) -> bool:
        return a == "right"

    async def broken(q: str, r: str, a: str) -> bool:
        raise RuntimeError("judge down")

    assert (await check_answer(task, "right", good)).correct
    failed = await check_answer(task, "right", broken)
    assert not failed.correct
    assert failed.error is not None
    assert "judge down" in failed.error
    assert not (await check_answer(task, "right", None)).correct
    num = {"question": "q", "check": {"type": "number", "value": 4, "rel_tol": 0.0}}
    assert (await check_answer(num, "4 payments", None)).method == "number"
