"""Optimizer middleware (contribution B) and metrics middleware.

``optimizer`` (response side): prunes fields of structured tool results (rules or a learned model),
optionally re-serializes them compactly, and keeps the removed data retrievable through the virtual
tool ``aegis__fetch_omitted`` (evidence recovery). Error results and the gateway's own virtual tools
are left untouched.

``metrics`` (response side, after the optimizer): records ``tokens_before`` / ``tokens_after``
(tiktoken, D6) and ``cost_saved_usd`` in ``ctx.extra``; the audit middleware stores them.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Protocol

from mcp.types import TextContent, Tool
from pydantic import BaseModel, ConfigDict, Field

from gateway.evidence import EvidenceStore
from gateway.optimizer.rules import PruningRules, prune
from gateway.optimizer.serialize import Format, render
from gateway.pipeline.base import Middleware
from gateway.pipeline.context import CallContext
from gateway.tokens import result_tokens

log = logging.getLogger(__name__)

VIRTUAL_SERVER = "aegis"
OMITTED_KEY = "_omitted"


def relax_schema(schema: Any) -> Any:
    """Copy of a JSON schema with every ``required`` list removed (pruned results omit fields).

    MCP clients validate ``structuredContent`` against the advertised ``outputSchema``; the gateway
    must not promise fields the optimizer may remove. Types and descriptions are kept.
    """
    if isinstance(schema, dict):
        return {k: relax_schema(v) for k, v in schema.items() if k != "required"}
    if isinstance(schema, list):
        return [relax_schema(v) for v in schema]
    return schema


class OptimizerSettings(BaseModel):
    """``middlewares.optimizer`` in gateway.yaml."""

    model_config = ConfigDict(extra="forbid")

    pruning: Literal["off", "rules", "learned"] = "rules"
    serialization: Format = "json"
    evidence_recovery: bool = True
    evidence_ttl_s: int = Field(default=900, gt=0)
    rules_file: str = "pruning_rules.yaml"
    """Relative to the gateway config file."""
    learned_model: str = "ml/artifacts/pruning_model.joblib"
    """Relative to the repository root (Phase 7)."""
    learned_threshold: float | None = None
    """Override the threshold saved with the learned model."""


class Pruner(Protocol):
    """Decides which field paths of a result to drop."""

    async def drop_paths(self, ctx: CallContext, data: dict[str, Any]) -> list[str]:
        """Paths (``a.b``, ``list[].field``) to remove from ``data``."""
        ...


class RulePruner:
    """Static per-tool rules from ``pruning_rules.yaml``."""

    def __init__(self, rules: PruningRules) -> None:
        self.rules = rules

    async def drop_paths(self, ctx: CallContext, data: dict[str, Any]) -> list[str]:
        """The configured paths for this tool."""
        return self.rules.for_tool(ctx.tool)


class OptimizerMiddleware(Middleware):
    """Prune + serialize + evidence recovery."""

    name = "optimizer"

    def __init__(
        self, settings: OptimizerSettings, pruner: Pruner | None, evidence: EvidenceStore | None
    ) -> None:
        self.settings = settings
        self.pruner = pruner
        self.evidence = evidence if settings.evidence_recovery else None

    async def on_list_tools(self, ctx: Any, tools: list[Tool]) -> list[Tool]:
        """Advertise relaxed output schemas for every downstream tool the optimizer may prune."""
        if self.pruner is None:
            return tools
        return [
            t.model_copy(update={"output_schema": relax_schema(t.output_schema)}) if t.output_schema else t
            for t in tools
        ]

    async def on_response(self, ctx: CallContext) -> None:
        """Rewrite a successful structured result."""
        result = ctx.result
        if result is None or result.is_error or ctx.server == VIRTUAL_SERVER:
            return
        data = result.structured_content
        if not isinstance(data, dict):
            return
        ctx.extra.setdefault("tokens_before", result_tokens(result))
        drop = await self.pruner.drop_paths(ctx, data) if self.pruner is not None else []
        pruned, removed = prune(data, drop) if drop else (dict(data), [])
        if removed and self.evidence is not None:
            ref = await self.evidence.put(data, self.settings.evidence_ttl_s)
            pruned[OMITTED_KEY] = {
                "ref": ref,
                "fields": removed,
                "how": "call aegis__fetch_omitted with this ref (and optional fields) to get them",
            }
        ctx.extra["pruned_fields"] = removed
        if not removed and self.settings.serialization == "json":
            return  # nothing changed: keep the server's own text
        text = render(pruned, self.settings.serialization)
        ctx.result = result.model_copy(
            update={"content": [TextContent(type="text", text=text)], "structured_content": pruned}
        )


class MetricsSettings(BaseModel):
    """``middlewares.metrics`` in gateway.yaml."""

    model_config = ConfigDict(extra="forbid")

    input_price_per_mtok_usd: float = Field(default=0.30, ge=0)
    """Price of one million input tokens of the agent's model (the saved tokens are input tokens)."""


class MetricsMiddleware(Middleware):
    """Token and cost metrics of the final result."""

    name = "metrics"

    def __init__(self, settings: MetricsSettings) -> None:
        self.settings = settings

    async def on_response(self, ctx: CallContext) -> None:
        """Record tokens before/after optimization and the cost saved."""
        if ctx.result is None:
            return
        after = result_tokens(ctx.result)
        before = int(ctx.extra.get("tokens_before", after))
        ctx.extra["tokens_before"] = before
        ctx.extra["tokens_after"] = after
        ctx.extra["cost_saved_usd"] = round(
            (before - after) * self.settings.input_price_per_mtok_usd / 1e6, 8
        )
