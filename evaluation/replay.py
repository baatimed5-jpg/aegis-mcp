"""Offline replay: exact token savings and gateway overhead of every profile, without any LLM call.

    uv run python -m evaluation.replay --source-tag baseline --tag replay

The corpus (default ``oracle``) is the reference tool calls of the 50 benchmark tasks
(``evaluation/oracle.py``), each sent with its question in ``_meta`` like the assistant does;
``--corpus recorded`` uses the calls the assistant really made in the source tag's gateway runs. Each call is
sent through an in-process gateway built from each profile's YAML (wealth server in-process, audit
in memory), so the only difference between profiles is the pipeline. Tool results are
deterministic (seeded data), so the token numbers are exact and need no repetition; latency is
measured over ``--repeats`` passes.

Output: ``evaluation/results/<tag>/replay_calls.csv`` (one row per profile x call) and
``replay_summary.csv``.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import statistics
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

from evaluation.oracle import corpus
from evaluation.report import percentile
from evaluation.runner import PROFILES_FILE, RESULTS_DIR, ROOT, _db
from gateway.app import Gateway
from gateway.config import load_config, load_env_file
from gateway.middlewares.registry import build_pipeline
from gateway.services import Services
from gateway.store import MemoryStore
from gateway.tokens import result_tokens
from servers.wealth.server import build_server

DEFAULT_PROFILES = [
    "security_only",
    "rule_optimizer",
    "learned_optimizer",
    "full",
    "order_filter_compress",
    "order_compress_filter",
]


Corpus = list[tuple[str, str, dict[str, Any]]]
"""(question, public tool name, arguments)."""


def oracle_corpus() -> Corpus:
    """Reference calls of every benchmark task."""
    return [(task["question"], tool, args) for task, tool, args in corpus()]


async def load_corpus(source: Path) -> Corpus:
    """(public tool name, arguments) of every allowed/approved call of the source gateway runs."""
    with source.open(encoding="utf-8", newline="") as f:
        run_ids = sorted({r["run_id"] for r in csv.DictReader(f) if r["profile"] != "no_gateway"})
    conn = await _db()
    try:
        rows = await conn.fetch(
            "SELECT server, tool, arguments FROM audit_call WHERE run_id = ANY($1::uuid[]) "
            "AND status IN ('allowed', 'approved') ORDER BY ts",
            [uuid.UUID(r) for r in run_ids],
        )
    finally:
        await conn.close()
    calls: Corpus = []
    for r in rows:
        args = r["arguments"]
        parsed = json.loads(args) if isinstance(args, str) else dict(args)
        calls.append(("", f"{r['server']}__{r['tool']}", parsed))
    return calls


async def replay_profile(name: str, gateway_yaml: Path, calls: Corpus, repeats: int) -> list[dict[str, Any]]:
    """Run the corpus through one profile; one row per call (tokens from pass 1, latency over all passes)."""
    config = load_config(gateway_yaml)
    config.database_url = None
    config.cache.enabled = False  # replay measures pruning; the cache would make repeats trivial
    config.redis_url = None
    store = MemoryStore()
    services = Services(config, store=store)
    pipeline = build_pipeline(config, services)
    rows: list[dict[str, Any]] = []
    approver_stop = asyncio.Event()

    async with Gateway(
        config, targets={"wealth": build_server()}, pipeline=pipeline, services=services
    ) as gw:

        async def approve_all() -> None:
            while not approver_stop.is_set():
                for item in services.approvals.pending() if services._approvals else []:
                    await services.approvals.decide(item.id, approve=True, decided_by="replay")
                await asyncio.sleep(0.01)

        approver = asyncio.create_task(approve_all())
        try:
            for i, (question, tool, args) in enumerate(calls):
                meta = {"aegis/question": question} if question else None
                latencies = []
                result = None
                for _ in range(repeats):
                    t0 = time.perf_counter()
                    result = await gw.call_tool("assistant", tool, dict(args), meta)
                    latencies.append((time.perf_counter() - t0) * 1000)
                assert result is not None
                audit = list(store.audit.values())[-1]
                final = result_tokens(result)
                before = audit["tokens_before"] if audit["tokens_before"] is not None else final
                after = audit["tokens_after"] if audit["tokens_after"] is not None else final
                overhead = (audit["latency_total_ms"] or 0) - (audit["latency_downstream_ms"] or 0)
                rows.append({
                    "profile": name, "call": i, "tool": tool,
                    "status": audit["status"], "is_error": result.is_error,
                    "tokens_before": before, "tokens_after": after,
                    "latency_ms_median": round(statistics.median(latencies), 3),
                    "gateway_ms": round(overhead, 3),
                })  # fmt: skip
        finally:
            approver_stop.set()
            await approver
    return rows


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per profile: total tokens before/after, savings ratio, gateway overhead percentiles."""
    out = []
    for profile in dict.fromkeys(r["profile"] for r in rows):
        rs = [r for r in rows if r["profile"] == profile]
        before = sum(r["tokens_before"] for r in rs)
        after = sum(r["tokens_after"] for r in rs)
        overhead = [r["gateway_ms"] for r in rs]
        out.append({
            "profile": profile, "calls": len(rs), "errors": sum(bool(r["is_error"]) for r in rs),
            "tokens_before": before, "tokens_after": after,
            "token_savings": round((before - after) / before, 4) if before else 0.0,
            "gateway_ms_p50": round(percentile(overhead, 50), 2),
            "gateway_ms_p95": round(percentile(overhead, 95), 2),
        })  # fmt: skip
    return out


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write dict rows with a header."""
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


async def main_async(args: argparse.Namespace) -> None:
    """Load the corpus, replay every available profile, write CSVs."""
    load_env_file(ROOT / ".env")
    for env in ("AGENT_TOKEN_ASSISTANT", "AGENT_TOKEN_REDTEAM"):
        os.environ.setdefault(env, uuid.uuid4().hex)
    cfg = yaml.safe_load(PROFILES_FILE.read_text(encoding="utf-8"))
    if args.corpus == "oracle":
        calls = oracle_corpus()
    else:
        calls = await load_corpus(RESULTS_DIR / args.source_tag / "tasks.csv")
    print(f"corpus: {len(calls)} tool calls ({args.corpus})", flush=True)
    rows: list[dict[str, Any]] = []
    for name in args.profiles:
        gateway_yaml = ROOT / cfg["profiles"][name]["gateway_config"]
        try:
            rows += await replay_profile(name, gateway_yaml, calls, args.repeats)
        except NotImplementedError as exc:
            print(f"[{name}] skipped: {exc}", flush=True)
            continue
        print(f"[{name}] done", flush=True)
    out = RESULTS_DIR / args.tag
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "replay_calls.csv", rows)
    summary = summarize(rows)
    write_csv(out / "replay_summary.csv", summary)
    for s in summary:
        print(s)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Replay recorded tool calls through each gateway profile")
    parser.add_argument("--source-tag", default="baseline")
    parser.add_argument("--tag", default="replay")
    parser.add_argument("--profiles", nargs="+", default=DEFAULT_PROFILES)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--corpus", choices=["oracle", "recorded"], default="oracle")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
