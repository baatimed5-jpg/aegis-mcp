"""Attack and false-positive evaluation of every gateway configuration (Phase 8).

    uv run python -m evaluation.attacks

Every red-team attack (``evaluation/attacks/attacks.jsonl``, both rounds) and every benign text is
returned by a test tool (``malicious__read_document``, in-process MCP server) and goes through the
gateway's real response pipeline, as an indirect injection would. A text counts as blocked when the
gateway records a security event for that call (sanitized or blocked). Benign texts: finance texts
with "risky" words (servers/malicious/payloads.py + ml/injection/synth.py) and 100 real free-text
strings of the wealth dataset. Configurations: no security (rule optimizer only), security with
each detector (rules, deberta, combined), and the full gateway.

    attack_block_rate   = blocked attacks / attacks
    false_positive_rate = blocked benign texts / benign texts
Output: evaluation/results/attacks/{results.csv, per_text.csv}.
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import uuid
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from gateway.app import Gateway
from gateway.config import GatewayConfig, load_config, load_env_file
from gateway.middlewares.registry import build_pipeline
from gateway.services import Services
from gateway.store import MemoryStore
from ml.injection.synth import BENIGN
from servers.malicious.payloads import BENIGN_TEXTS
from servers.wealth.data import generate_dataset

ROOT = Path(__file__).resolve().parents[1]
ATTACKS = ROOT / "evaluation" / "attacks" / "attacks.jsonl"
OUT = ROOT / "evaluation" / "results" / "attacks"


def document_server(texts: list[str]) -> MCPServer:
    """Test server whose only tool returns document ``i`` (stands for any untrusted tool output)."""
    mcp = MCPServer(name="malicious")

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
    def read_document(i: int) -> dict[str, Any]:
        """Return a stored document (news, note or comment)."""
        return {"doc_id": i, "content": texts[i]}

    return mcp


def benign_texts() -> list[str]:
    """Benign finance texts with risky words + real dataset strings (seeded order)."""
    ds = generate_dataset()
    real = sorted({t.comment for txs in ds.transactions.values() for t in txs if len(t.comment) > 20})[:100]
    filled = [b.format(amt="25,000", pf="PF-00012", tick="ACME", date="2026-05-14") for b in BENIGN]
    return [*BENIGN_TEXTS, *filled, *real]


CONFIGS: dict[str, tuple[str, str | None]] = {
    # name: (gateway yaml, detector override)
    "no_security": ("config/profiles/rule_optimizer.yaml", None),
    "security_rules": ("config/profiles/security_only.yaml", "rules"),
    "security_deberta": ("config/profiles/security_only.yaml", "deberta"),
    "security_combined": ("config/profiles/security_only.yaml", "combined"),
    "full_combined": ("config/profiles/full.yaml", "combined"),
}


def _config(path: str, detector: str | None) -> GatewayConfig:
    config = load_config(ROOT / path)
    config.database_url = None
    config.redis_url = None
    config.cache.enabled = False
    config.servers = {"malicious": config.servers["wealth"].model_copy(update={"url": "http://unused/mcp"})}
    if detector is not None and "injection_scan" in config.middlewares:
        config.middlewares["injection_scan"] = {**config.middlewares["injection_scan"], "detector": detector}
    return config


async def run_config(name: str, texts: list[str]) -> list[bool]:
    """For each text: did the gateway raise a security event on the call that returned it?"""
    path, detector = CONFIGS[name]
    config = _config(path, detector)
    store = MemoryStore()
    services = Services(config, store=store)
    pipeline = build_pipeline(config, services)
    flagged: list[bool] = []
    async with Gateway(
        config, targets={"malicious": document_server(texts)}, pipeline=pipeline, services=services
    ) as gw:
        for i in range(len(texts)):
            before = len(store.events)
            await gw.call_tool("assistant", "malicious__read_document", {"i": i})
            new = store.events[before:]
            flagged.append(any(e["kind"] in ("response", "argument") for e in new))
    return flagged


async def main_async() -> None:
    """Evaluate every configuration on attacks + benign texts."""
    load_env_file(ROOT / ".env")
    for env in ("AGENT_TOKEN_ASSISTANT", "AGENT_TOKEN_REDTEAM"):
        os.environ.setdefault(env, uuid.uuid4().hex)
    attacks = [json.loads(line) for line in ATTACKS.read_text(encoding="utf-8").splitlines() if line]
    benign = benign_texts()
    texts = [a["text"] for a in attacks] + benign
    results, per_text = [], []
    for name in CONFIGS:
        flagged = await run_config(name, texts)
        att, ben = flagged[: len(attacks)], flagged[len(attacks) :]
        r1 = [f for a, f in zip(attacks, att, strict=True) if a["round"] == 1]
        r2 = [f for a, f in zip(attacks, att, strict=True) if a["round"] == 2]
        results.append({
            "config": name,
            "attack_block_rate": round(sum(att) / len(att), 4), "attacks": len(att),
            "block_rate_round1": round(sum(r1) / len(r1), 4) if r1 else "",
            "block_rate_adaptive": round(sum(r2) / len(r2), 4) if r2 else "", "adaptive_n": len(r2),
            "false_positive_rate": round(sum(ben) / len(ben), 4), "benign": len(ben),
        })  # fmt: skip
        for a, f in zip(attacks, att, strict=True):
            per_text.append({"config": name, "kind": "attack", "id": a["id"], "round": a["round"],
                             "technique": a["technique"], "blocked": f})  # fmt: skip
        print(results[-1], flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    for fname, rows in (("results.csv", results), ("per_text.csv", per_text)):
        with (OUT / fname).open("w", encoding="utf-8", newline="") as handle:
            w = csv.DictWriter(handle, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)


if __name__ == "__main__":
    asyncio.run(main_async())
