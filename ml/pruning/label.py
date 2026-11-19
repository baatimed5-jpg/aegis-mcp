"""LLM-as-labeler: which fields of each tool result are needed to answer the task's question.

    uv run python -m ml.pruning.label [--model gemini/gemini-3.1-flash-lite]

For every reference call of the 50 benchmark tasks (``evaluation/oracle.py``) the real tool result
is produced by the wealth service, its candidate field paths are listed with an example value, and
the LLM returns the paths needed. One request per call; results cached in
``ml/data/pruning/labels.jsonl`` (re-runs only label what is missing). A deterministic heuristic
label (the expected answer value appears in the field) is stored too, as a sanity check.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from evaluation.oracle import corpus
from gateway.config import load_env_file
from gateway.llm import complete
from gateway.optimizer.fields import candidate_paths
from servers.wealth.server import build_server

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "ml" / "data" / "pruning"
LABELS = OUT / "labels.jsonl"

PROMPT = """A banking assistant must answer this QUESTION using the result of the tool `{tool}`.
Below are the result's fields (path = example value). List the paths whose values the assistant
needs to answer correctly (include ids/names needed to identify the right item, and fields needed to
compare or rank items). Do not include fields that are irrelevant to the question.
Reply with JSON only: a list of paths, exactly as written below.

QUESTION: {question}

FIELDS:
{fields}"""


async def tool_result(tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """Structured result of a wealth tool (in-process MCP call), None for tool errors."""
    from mcp import Client

    async with Client(build_server()) as client:
        res = await client.call_tool(tool.removeprefix("wealth__"), args)
    return (
        None if res.is_error or not isinstance(res.structured_content, dict) else dict(res.structured_content)
    )


def heuristic_needed(task: dict[str, Any], paths: dict[str, Any], data: dict[str, Any]) -> list[str]:
    """Paths whose (first) value appears in the expected answer spec, as a cheap cross-check."""
    spec = task["check"]
    targets: list[str] = []
    if spec["type"] == "number":
        targets.append(str(spec["value"]))
    elif spec["type"] == "text":
        targets += [a for g in spec["all_of"] for a in g]
    blob = json.dumps(data)
    needed = []
    for path in paths:
        leaf = path.split(".")[-1]
        for t in targets:
            if re.search(rf'"{re.escape(leaf)}":\s*"?{re.escape(t)}', blob):
                needed.append(path)
                break
    return needed


def ask_llm(model: str, question: str, tool: str, paths: dict[str, Any]) -> list[str]:
    """Paths the LLM considers necessary (only known paths are kept)."""
    fields = "\n".join(f"- {p} = {json.dumps(v, default=str)[:80]}" for p, v in paths.items())
    reply = complete(model, PROMPT.format(tool=tool, question=question, fields=fields))
    match = re.search(r"\[.*\]", reply, re.DOTALL)
    if not match:
        raise ValueError(f"labeler reply has no JSON list: {reply[:200]!r}")
    return [p for p in json.loads(match.group(0)) if p in paths]


def main() -> None:
    """Label every reference call (cached)."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gemini/gemini-3.1-flash-lite")
    args = parser.parse_args()
    load_env_file(ROOT / ".env")
    OUT.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if LABELS.exists():
        done = {json.loads(line)["key"] for line in LABELS.read_text(encoding="utf-8").splitlines() if line}
    with LABELS.open("a", encoding="utf-8") as f:
        for i, (task, tool, call_args) in enumerate(corpus()):
            key = f"{task['id']}|{tool}|{json.dumps(call_args, sort_keys=True)}"
            if key in done:
                continue
            data = asyncio.run(tool_result(tool, call_args))
            if data is None:
                continue
            paths = candidate_paths(data)
            needed = ask_llm(args.model, task["question"], tool, paths)
            row = {"key": key, "task_id": task["id"], "category": task["category"], "question": task["question"],
                   "tool": tool.removeprefix("wealth__"), "paths": list(paths), "needed_llm": needed,
                   "needed_heuristic": heuristic_needed(task, paths, data), "labeler": args.model}  # fmt: skip
            f.write(json.dumps(row) + "\n")
            f.flush()
            print(f"{i:3d} {task['id']} {tool:<35} {len(needed)}/{len(paths)} needed", flush=True)


if __name__ == "__main__":
    main()
