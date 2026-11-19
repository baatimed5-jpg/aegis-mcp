"""Unit tests: reference tool calls of the benchmark answer the questions correctly."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp import Client

from evaluation.build_tasks import OUT
from evaluation.checkers import check_number, check_text
from evaluation.oracle import corpus, oracle_calls
from servers.wealth.server import build_server


def _tasks() -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = json.loads(OUT.read_text(encoding="utf-8"))
    return tasks


def test_every_task_has_reference_calls() -> None:
    tasks = _tasks()
    assert all(oracle_calls(t) for t in tasks)
    assert len(corpus()) >= len(tasks)


def test_reference_results_contain_the_expected_answer() -> None:
    """The raw results of the reference calls hold every automatically checked answer."""

    async def results(task: dict[str, Any]) -> str:
        texts = []
        async with Client(build_server()) as client:
            for tool, args in oracle_calls(task):
                res = await client.call_tool(tool.removeprefix("wealth__"), args)
                texts.append(json.dumps(res.structured_content))
        return "\n".join(texts)

    for task in _tasks():
        spec = task["check"]
        if spec["type"] == "judge" or task["category"] in (
            "holding_count",
            "client_total_value",
            "dividend_count",
        ):
            continue  # counts/sums are derived by the agent, not literally present
        blob = asyncio.run(results(task))
        if spec["type"] == "number":
            assert check_number(blob, spec), task["id"]
        else:
            assert check_text(blob, spec) or task["category"] == "last_sell", task["id"]
