"""Evaluation runner: run the 50 benchmark tasks for one or more profiles, N times each.

    uv run python -m evaluation.runner --profiles no_gateway security_only --runs 3

For each profile the runner starts the wealth server (and the gateway when the profile needs it)
as subprocesses, runs every task through the assistant, checks the answers and appends one CSV row
per (run, task) to ``evaluation/results/<tag>/tasks.csv``. Gateway profiles also export their audit
rows (tagged with the experiment run id sent in ``_meta``) to ``gateway_calls.csv``.

Nothing is cached between tasks: each task opens a fresh agent run (same MCP session per run).
Failures (rate limits, timeouts, max turns) are recorded as failed tasks with the error text.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import tiktoken
import yaml
from agents import Agent, RunContextWrapper, RunHooks, Runner, Tool
from agents.items import ToolCallOutputItem, TResponseInputItem
from sqlalchemy.engine import make_url

from aegis_agents.assistant.agent import AssistantSettings, TaskContext, build_assistant, mcp_connection
from evaluation.checkers import JudgeFn, check_answer, litellm_judge
from gateway.config import load_env_file

ROOT = Path(__file__).resolve().parents[1]
TASKS_FILE = ROOT / "evaluation" / "benchmark" / "tasks.json"
PROFILES_FILE = ROOT / "config" / "eval_profiles.yaml"
RESULTS_DIR = ROOT / "evaluation" / "results"
ENCODING = tiktoken.get_encoding("o200k_base")
RATE_LIMIT_RETRIES = 4
RATE_LIMIT_WAIT_S = 65.0


@dataclass
class TaskRow:
    """One (profile, run, task) result, written as a CSV row."""

    profile: str
    run_index: int
    run_id: str
    task_id: str
    category: str
    correct: bool
    check_method: str
    error: str
    llm_requests: int
    input_tokens: int
    output_tokens: int
    tool_calls: int
    tool_errors: int
    tool_output_tokens: int
    """tiktoken o200k_base count of the tool outputs the model saw (approximation, D6)."""
    tool_latency_ms_mean: float
    tool_latencies_ms: str
    """Client-side latency of every tool call of the task (space-separated)."""
    wall_s: float
    tools_used: str
    answer: str
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class TimingHooks(RunHooks[TaskContext]):
    """Measures each tool call (client side) and throttles LLM requests."""

    def __init__(self, min_interval_s: float) -> None:
        self.min_interval_s = min_interval_s
        self._last_request = 0.0
        self._starts: dict[str, float] = {}
        self.latencies_ms: list[float] = []

    async def on_llm_start(
        self,
        context: RunContextWrapper[TaskContext],
        agent: Agent[TaskContext],
        system_prompt: str | None,
        input_items: list[TResponseInputItem],
    ) -> None:
        """Wait so that LLM requests are at least ``min_interval_s`` apart (free-tier limits)."""
        wait = self._last_request + self.min_interval_s - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request = time.monotonic()

    async def on_tool_start(
        self, context: RunContextWrapper[TaskContext], agent: Agent[TaskContext], tool: Tool
    ) -> None:
        """Remember when this tool call started."""
        self._starts[str(getattr(context, "tool_call_id", id(tool)))] = time.perf_counter()

    async def on_tool_end(
        self, context: RunContextWrapper[TaskContext], agent: Agent[TaskContext], tool: Tool, result: object
    ) -> None:
        """Record the tool call latency."""
        start = self._starts.pop(str(getattr(context, "tool_call_id", id(tool))), None)
        if start is not None:
            self.latencies_ms.append((time.perf_counter() - start) * 1000)


def tool_output_text(output: Any) -> str:
    """Model-visible text of a tool output: a string, or the text parts of a content list."""
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        return "\n".join(str(p.get("text", "")) for p in output if isinstance(p, dict))
    return json.dumps(output, default=str)


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


class ConfigurationError(RuntimeError):
    """The provider rejected the model or key: every task would fail, so the run stops."""


_RETRYABLE = (
    "ratelimit", "429", "resource_exhausted", "quota", "503", "serviceunavailable", "high demand",
    "apiconnectionerror", "cannot connect", "timeout", "connectionreset", "500", "internalservererror",
)  # fmt: skip
AGENT_FAILURES = ("MaxTurnsExceeded",)
"""Errors that are the agent's own failure (counted as wrong answers, never re-run on resume)."""


def is_infra_error(error: str) -> bool:
    """True for provider/network errors (re-run on resume, reported separately from wrong answers)."""
    return bool(error) and not error.startswith(AGENT_FAILURES)


_FATAL = (
    "notfounderror", "authenticationerror", "unsupportedparamserror", "api key not valid", "permission",
    "perday",  # daily free-tier quota (e.g. GenerateRequestsPerDayPerProjectPerModel): waiting will not help
)  # fmt: skip


def _is_retryable(exc: BaseException) -> bool:
    """Rate limits and temporary overload (429/503): wait and run the task again."""
    text = f"{type(exc).__name__} {exc}".lower()
    return any(marker in text for marker in _RETRYABLE)


def _is_fatal(exc: BaseException) -> bool:
    """Wrong model id, bad key or unsupported parameter: no point running the other tasks."""
    text = f"{type(exc).__name__} {exc}".lower()
    return any(marker in text for marker in _FATAL)


async def run_task(
    agent: Agent[TaskContext], task: dict[str, Any], *, profile: str, run_index: int, run_id: str,
    max_turns: int, min_interval_s: float, judge: JudgeFn | None,
) -> TaskRow:  # fmt: skip
    """Run one task, retrying the whole task on provider rate limits, then check the answer."""
    error = ""
    answer = ""
    usage_in = usage_out = requests = 0
    hooks = TimingHooks(min_interval_s)
    ctx = TaskContext(question=task["question"], run_id=run_id)
    outputs: list[Any] = []
    started = time.perf_counter()
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        hooks = TimingHooks(min_interval_s)
        ctx = TaskContext(question=task["question"], run_id=run_id)
        try:
            result = await Runner.run(agent, task["question"], context=ctx, max_turns=max_turns, hooks=hooks)
            answer = str(result.final_output or "")
            usage = result.context_wrapper.usage
            usage_in, usage_out, requests = usage.input_tokens, usage.output_tokens, usage.requests
            outputs = [
                item.raw_item.get("output", "") if isinstance(item.raw_item, dict) else item.output
                for item in result.new_items
                if isinstance(item, ToolCallOutputItem)
            ]
            error = ""
            break
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
            if _is_fatal(exc):
                raise ConfigurationError(error) from exc
            if _is_retryable(exc) and attempt < RATE_LIMIT_RETRIES:
                print(
                    f"    rate limited/overloaded, waiting {RATE_LIMIT_WAIT_S:.0f} s (attempt {attempt + 1})",
                    flush=True,
                )
                await asyncio.sleep(RATE_LIMIT_WAIT_S)
                continue
            break
    wall_s = time.perf_counter() - started
    verdict = await check_answer(task, answer, judge) if answer else None
    texts = [tool_output_text(o) for o in outputs]
    return TaskRow(
        profile=profile,
        run_index=run_index,
        run_id=run_id,
        task_id=task["id"],
        category=task["category"],
        correct=bool(verdict and verdict.correct and not error),
        check_method=verdict.method if verdict else task["check"]["type"],
        error=error or (verdict.error or "" if verdict else "no answer"),
        llm_requests=requests,
        input_tokens=usage_in,
        output_tokens=usage_out,
        tool_calls=len(ctx.tool_calls),
        tool_errors=sum(bool(t.get("is_error")) for t in ctx.tool_calls),
        tool_output_tokens=sum(len(ENCODING.encode(t)) for t in texts),
        tool_latency_ms_mean=_mean(hooks.latencies_ms),
        tool_latencies_ms=" ".join(f"{v:.1f}" for v in hooks.latencies_ms),
        wall_s=round(wall_s, 2),
        tools_used=" ".join(t["tool"] for t in ctx.tool_calls),
        answer=answer[:1000],
    )  # fmt: skip


# ---- infrastructure --------------------------------------------------------------------------


def _port_open(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return True
    except OSError:
        return False


def _wait_port(port: int, proc: subprocess.Popen[bytes], what: str, timeout_s: float = 90) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"{what} exited early with code {proc.returncode} (see {what}.log)")
        if _port_open(port):
            return
        time.sleep(0.3)
    raise RuntimeError(f"{what} did not start within {timeout_s:g} s")


@contextmanager
def services(profile: dict[str, Any], cfg: dict[str, Any], log_dir: Path) -> Iterator[None]:
    """Start the wealth server and, if needed, the gateway; stop them afterwards."""
    procs: list[subprocess.Popen[bytes]] = []
    logs = []
    wanted = [("wealth", cfg["wealth_port"], ["-m", "servers.wealth", "--port", str(cfg["wealth_port"])])]
    if profile.get("gateway_config"):
        args = [
            "-m",
            "gateway",
            "--config",
            str(ROOT / profile["gateway_config"]),
            "--port",
            str(cfg["gateway_port"]),
        ]
        wanted.append(("gateway", cfg["gateway_port"], args))
    try:
        for what, port, args in wanted:
            if _port_open(port):
                raise RuntimeError(f"port {port} is busy: stop the running {what} first")
            log = (log_dir / f"{what}.log").open("wb")
            logs.append(log)
            proc = subprocess.Popen([sys.executable, *args], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            procs.append(proc)
            _wait_port(port, proc, what)
        yield
    finally:
        for proc in reversed(procs):
            _stop_tree(proc)
        for log in logs:
            log.close()
        for _what, port, _args in wanted:
            _wait_port_free(port)


def _stop_tree(proc: subprocess.Popen[bytes]) -> None:
    """Stop a server and its children (on Windows the venv python.exe is a launcher with a child)."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


def _wait_port_free(port: int, timeout_s: float = 20) -> None:
    """Wait until nothing listens on ``port`` any more (the next profile reuses it)."""
    deadline = time.monotonic() + timeout_s
    while _port_open(port) and time.monotonic() < deadline:
        time.sleep(0.3)


async def _db() -> asyncpg.Connection:
    url = make_url(os.environ["DATABASE_URL"])
    return await asyncpg.connect(
        user=url.username, password=url.password, host=url.host, port=url.port, database=url.database
    )


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        )
        return out.stdout.strip()[:40]
    except (OSError, subprocess.CalledProcessError):
        return None


async def create_experiment_run(config_name: str, run_index: int) -> str:
    """Insert an ``experiment_run`` row; audit rows of this run reference it."""
    run_id = uuid.uuid4()
    conn = await _db()
    try:
        await conn.execute(
            "INSERT INTO experiment_run (id, config_name, run_index, git_sha, started_at) "
            "VALUES ($1, $2, $3, $4, $5)",
            run_id, config_name, run_index, _git_sha(), datetime.now(UTC),
        )  # fmt: skip
    finally:
        await conn.close()
    return str(run_id)


AUDIT_COLUMNS = [
    "run_id", "ts", "tool", "status", "reason", "injection_score", "pii_entities",
    "latency_total_ms", "latency_downstream_ms",
]  # fmt: skip


async def export_audit(run_ids: list[str], path: Path, profile: str) -> int:
    """Append the gateway audit rows of these runs to a CSV; returns the row count."""
    conn = await _db()
    try:
        rows = await conn.fetch(
            f"SELECT {', '.join(AUDIT_COLUMNS)} FROM audit_call WHERE run_id = ANY($1::uuid[]) ORDER BY ts",
            [uuid.UUID(r) for r in run_ids],
        )
    finally:
        await conn.close()
    _replace_profile_rows(path, profile, [[profile, *(r[c] for c in AUDIT_COLUMNS)] for r in rows])
    return len(rows)


def _replace_profile_rows(path: Path, profile: str, rows: list[list[Any]]) -> None:
    """Rewrite the audit CSV with this profile's rows replaced (safe to re-export after a resume)."""
    kept: list[list[str]] = []
    if path.exists():
        with path.open(encoding="utf-8", newline="") as f:
            kept = [r for r in list(csv.reader(f))[1:] if r and r[0] != profile]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["profile", *AUDIT_COLUMNS])
        w.writerows(kept)
        w.writerows(rows)


async def auto_approver(admin_url: str, token: str, stop: asyncio.Event) -> None:
    """Approve every pending approval (evaluation only; the call itself still goes through the queue)."""
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(headers=headers, timeout=10) as client:
        while not stop.is_set():
            try:
                resp = await client.get(admin_url)
                resp.raise_for_status()
                for item in resp.json():
                    await client.post(
                        f"{admin_url}/{item['id']}", json={"decision": "approve", "decided_by": "eval-auto"}
                    )
            except httpx.HTTPError as exc:
                print(f"    auto-approver: {exc}", flush=True)
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=0.5)


# ---- main loop -------------------------------------------------------------------------------


def write_rows(path: Path, rows: list[TaskRow]) -> None:
    """Append task rows to the CSV (header on first write)."""
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(TaskRow.__dataclass_fields__))
        if new:
            w.writeheader()
        for row in rows:
            w.writerow(asdict(row))


def completed_tasks(path: Path, profile: str) -> tuple[set[tuple[int, str]], dict[int, str]]:
    """Resume support: tasks already finished without an infrastructure error, and the run ids used.

    A task whose row has an error (rate limit, overload, timeout) is run again; the report keeps the
    last row of each (profile, run, task).
    """
    done: set[tuple[int, str]] = set()
    run_ids: dict[int, str] = {}
    if not path.exists():
        return done, run_ids
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["profile"] != profile:
                continue
            run_ids[int(row["run_index"])] = row["run_id"]
            if not is_infra_error(row["error"]):
                done.add((int(row["run_index"]), row["task_id"]))
    return done, run_ids


async def run_profile(
    name: str, cfg: dict[str, Any], tasks: list[dict[str, Any]], out_dir: Path, runs: int,
    model: str, judge: JudgeFn | None,
) -> None:  # fmt: skip
    """All runs of one profile."""
    profile = cfg["profiles"][name]
    is_gateway = bool(profile.get("gateway_config"))
    settings = AssistantSettings(
        mcp_url=profile["mcp_url"],
        model=model,
        token_env=profile.get("token_env"),
        api_key_env=cfg["api_key_env"],
    )
    run_ids: list[str] = []
    done, previous_ids = completed_tasks(out_dir / "tasks.csv", name)
    with services(profile, cfg, out_dir):
        for run_index in range(runs):
            todo = [t for t in tasks if (run_index, t["id"]) not in done]
            run_id = previous_ids.get(run_index) or (
                await create_experiment_run(name, run_index) if is_gateway else str(uuid.uuid4())
            )
            run_ids.append(run_id)
            print(f"[{name}] run {run_index + 1}/{runs} ({run_id}): {len(todo)} tasks to do", flush=True)
            if not todo:
                continue
            stop = asyncio.Event()
            approver = None
            if profile.get("auto_approve"):
                admin = f"http://127.0.0.1:{cfg['gateway_port']}/api/approvals"
                approver = asyncio.create_task(auto_approver(admin, os.environ["ADMIN_TOKEN"], stop))
            try:
                async with mcp_connection(settings) as server:
                    agent = build_assistant(settings, server)
                    for task in todo:
                        row = await run_task(
                            agent, task, profile=name, run_index=run_index, run_id=run_id,
                            max_turns=cfg["max_turns"], min_interval_s=cfg["min_request_interval_s"],
                            judge=judge,
                        )  # fmt: skip
                        write_rows(out_dir / "tasks.csv", [row])
                        mark = "ok " if row.correct else "BAD"
                        print(f"  {mark} {task['id']} {task['category']:<20} in={row.input_tokens:>6} "
                              f"tools={row.tool_calls} {row.error[:80]}", flush=True)  # fmt: skip
            finally:
                stop.set()
                if approver:
                    await approver
    if is_gateway:
        n = await export_audit(run_ids, out_dir / "gateway_calls.csv", name)
        print(f"[{name}] exported {n} audit rows", flush=True)


async def main_async(args: argparse.Namespace) -> None:
    """Load config and tasks, then run the selected profiles."""
    load_env_file(ROOT / ".env")
    cfg = yaml.safe_load(PROFILES_FILE.read_text(encoding="utf-8"))
    if args.min_interval is not None:
        cfg["min_request_interval_s"] = args.min_interval
    tasks: list[dict[str, Any]] = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    if args.tasks:
        wanted = set(args.tasks)
        tasks = [t for t in tasks if t["id"] in wanted]
    model = args.model or cfg["model"]
    api_key = os.environ.get(cfg["api_key_env"], "")
    judge = litellm_judge(cfg["judge_model"], api_key) if api_key else None
    out_dir = RESULTS_DIR / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {"model": model, "judge_model": cfg["judge_model"], "runs": args.runs, "tasks": len(tasks),
            "profiles": args.profiles, "git_sha": _git_sha(),
            "started_at": datetime.now(UTC).isoformat()}  # fmt: skip
    (out_dir / f"meta_{datetime.now(UTC):%Y%m%dT%H%M%S}.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    for name in args.profiles:
        await run_profile(name, cfg, tasks, out_dir, args.runs, model, judge)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Run the AegisMCP assistant benchmark")
    parser.add_argument("--profiles", nargs="+", default=["no_gateway", "security_only"])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--tag", default="baseline", help="results sub-folder")
    parser.add_argument("--tasks", nargs="*", help="only these task ids (smoke tests)")
    parser.add_argument("--model", help="override the LiteLLM model id")
    parser.add_argument("--min-interval", type=float, help="seconds between LLM requests")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
