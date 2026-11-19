"""Unit tests: report aggregation math (synthetic rows, not results)."""

from __future__ import annotations

import pytest

from evaluation.report import gateway_overhead, mean_std, percentile, render, summarize


def _row(
    profile: str, run: int, correct: bool, tokens: int, lat: str = "10 20", task: str = "T01"
) -> dict[str, str]:
    return {
        "profile": profile, "run_index": str(run), "task_id": task, "category": "c", "correct": str(correct),
        "error": "", "llm_requests": "2", "input_tokens": str(tokens), "output_tokens": "100",
        "tool_calls": "1", "tool_output_tokens": "50", "wall_s": "1.5", "tool_latencies_ms": lat,
    }  # fmt: skip


def test_percentile_and_mean_std() -> None:
    assert percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)
    assert percentile([5], 95) == 5
    m, sd = mean_std([0.5, 1.0])
    assert m == pytest.approx(0.75)
    assert sd == pytest.approx(0.35355, rel=1e-4)
    assert mean_std([2.0]) == (2.0, 0.0)


def test_summarize_per_run_success_and_cost() -> None:
    rows = [
        _row("p", 0, True, 1000),
        _row("p", 0, False, 3000, task="T02"),
        _row("p", 1, True, 1000),
        _row("p", 1, True, 1000, task="T02"),
    ]
    s = summarize(rows, {"input": 1.0, "output": 10.0})["p"]
    assert s["runs"] == 2
    assert s["metrics"]["task_success_rate"][0] == pytest.approx(0.75)  # runs: 0.5 and 1.0
    # cost per task = (in * 1 + 100 * 10) / 1e6; run 0 mean in = 2000, run 1 = 1000
    assert s["metrics"]["cost_usd_per_task"][0] == pytest.approx(((3000 + 2000) / 2) / 1e6)
    assert s["client_tool_latency_ms"][2] == 8


def test_gateway_overhead_and_render() -> None:
    calls = [
        {"profile": "g", "latency_total_ms": "12", "latency_downstream_ms": "10", "status": "ok"},
        {"profile": "g", "latency_total_ms": "15", "latency_downstream_ms": "", "status": "denied"},
    ]
    ov = gateway_overhead(calls)
    assert ov["g"][0] == pytest.approx(2.0)
    assert ov["g"][3] == {"ok": 1, "denied": 1}
    summary = summarize([_row("g", 0, True, 10)], {"input": 0.0, "output": 0.0})
    text = render("baseline", {"model": "m"}, summary, ov)
    assert "<!-- section:baseline -->" in text
    assert "| Task success rate | 100.0% ± 0.0% |" in text


def test_resumed_task_last_row_wins() -> None:
    failed = {**_row("p", 0, False, 0), "error": "RateLimitError"}
    rows = [failed, _row("p", 0, True, 10)]
    s = summarize(rows, {"input": 0.0, "output": 0.0})["p"]
    assert s["metrics"]["task_success_rate"][0] == 1.0
    assert s["metrics"]["errors"][0] == 0.0
