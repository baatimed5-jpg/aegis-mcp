"""Unit tests: runner helpers (resume bookkeeping, error classification, tool-output text)."""

from __future__ import annotations

from pathlib import Path

from evaluation.runner import _is_fatal, _is_retryable, completed_tasks, tool_output_text


def test_completed_tasks_skips_only_clean_rows(tmp_path: Path) -> None:
    path = tmp_path / "tasks.csv"
    path.write_text(
        "profile,run_index,run_id,task_id,error\n"
        "a,0,r0,T01,\n"
        "a,0,r0,T02,RateLimitError: 429\n"
        "a,1,r1,T01,\n"
        "b,0,x0,T01,\n",
        encoding="utf-8",
    )
    done, ids = completed_tasks(path, "a")
    assert done == {(0, "T01"), (1, "T01")}
    assert ids == {0: "r0", 1: "r1"}
    assert completed_tasks(tmp_path / "missing.csv", "a") == (set(), {})


def test_error_classification() -> None:
    assert _is_retryable(RuntimeError("503 This model is currently experiencing high demand"))
    assert _is_retryable(RuntimeError("RateLimitError 429 GenerateRequestsPerMinute"))
    assert _is_fatal(RuntimeError("quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier"))
    assert _is_fatal(RuntimeError("NotFoundError: model is no longer available"))
    assert not _is_fatal(RuntimeError("503 high demand"))


def test_tool_output_text() -> None:
    assert tool_output_text("abc") == "abc"
    assert (
        tool_output_text([{"type": "input_text", "text": "a"}, {"type": "input_text", "text": "b"}]) == "a\nb"
    )
    assert tool_output_text({"x": 1}) == '{"x": 1}'
