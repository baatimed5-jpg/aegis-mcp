"""Injection-scan middleware: tool descriptions, call arguments and tool responses (D10).

- Descriptions (tool poisoning): scanned when tools are listed or called, cached by content hash.
  ``hide`` removes the tool from ``tools/list`` and blocks its calls; ``sanitize`` replaces the
  flagged text; ``flag`` only records an event.
- Arguments: ``block`` stops the call; ``flag`` records it.
- Responses (indirect injection): every string value is scored; ``sanitize`` replaces flagged
  values with a placeholder, ``block`` replaces the whole result with an error, ``flag`` records.

The highest score of the call goes to ``ctx.extra["injection_score"]`` (audit column) and every
detection to ``ctx.extra["security_events"]`` (written by the audit middleware).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

import anyio
from mcp.types import Tool
from pydantic import BaseModel, ConfigDict, Field

from gateway.detectors.base import Detection, InjectionDetector
from gateway.middlewares.audit import SECURITY_EVENTS_KEY
from gateway.namespacing import split
from gateway.pipeline.base import Middleware
from gateway.pipeline.content import Path, iter_strings, map_result_strings, map_strings, result_strings
from gateway.pipeline.context import CallContext, ListContext
from gateway.pipeline.errors import PipelineDenied
from gateway.store import GatewayStore

log = logging.getLogger(__name__)

PLACEHOLDER = "[removed by AegisMCP: possible prompt injection]"
EXCERPT_CHARS = 300
_SCHEMA_TEXT_KEYS = {"description", "title"}


class InjectionScanSettings(BaseModel):
    """``middlewares.injection_scan`` in gateway.yaml."""

    model_config = ConfigDict(extra="forbid")

    detector: Literal["rules", "deberta", "llm", "combined"] = "rules"
    model_path: str = "ml/artifacts/injection-deberta"
    """DeBERTa folder (relative to the repository root)."""
    combined_file: str = "ml/artifacts/injection-combined.json"
    llm_model: str = "gemini/gemini-3.1-flash-lite"
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    on_description: Literal["hide", "sanitize", "flag", "off"] = "hide"
    on_argument: Literal["block", "flag", "off"] = "block"
    on_response: Literal["sanitize", "block", "flag", "off"] = "sanitize"


def _event(
    kind: str, server: str, tool: str, detector: str, found: Detection, action: str, text: str
) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "ts": datetime.now(UTC),
        "kind": kind,
        "server": server,
        "tool": tool,
        "detector": detector,
        "score": found.score,
        "action": action,
        "excerpt": f"[{', '.join(found.matches)}] {text[:EXCERPT_CHARS]}",
    }


def _schema_texts(schema: dict[str, Any]) -> list[str]:
    """Human-readable strings of an input schema (property descriptions and titles)."""
    return [value for path, value in iter_strings(schema) if path and path[-1] in _SCHEMA_TEXT_KEYS]


class InjectionScanMiddleware(Middleware):
    """Scans what crosses the gateway with an ``InjectionDetector``."""

    name = "injection_scan"

    def __init__(
        self, detector: InjectionDetector, settings: InjectionScanSettings, store: GatewayStore | None = None
    ) -> None:
        self.detector = detector
        self.settings = settings
        self.store = store
        self._descriptions: dict[tuple[str, str], Detection] = {}

    def _flagged(self, found: Detection) -> bool:
        return found.score >= self.settings.threshold

    # ---- tool descriptions -------------------------------------------------------------------
    async def _scan_tool(self, tool: Tool) -> Detection:
        """Score a tool's description + schema texts; cached, and the first detection is recorded."""
        texts = [tool.description or "", tool.title or "", *_schema_texts(tool.input_schema)]
        digest = hashlib.sha256("\x00".join(texts).encode()).hexdigest()
        key = (tool.name, digest)
        cached = self._descriptions.get(key)
        if cached is not None:
            return cached
        results = await anyio.to_thread.run_sync(self.detector.score_many, [t for t in texts if t])
        found = max(results, key=lambda d: d.score, default=Detection(0.0))
        self._descriptions[key] = found
        if self._flagged(found):
            try:
                server, name = split(tool.name)
            except ValueError:
                server, name = "", tool.name
            text = next((t for t, d in zip([t for t in texts if t], results, strict=True) if d is found), "")
            log.warning(
                "tool %s: description flagged (score %.2f, %s)", tool.name, found.score, found.matches
            )
            if self.store is not None:
                event = _event("description", server, name, self.detector.name, found,
                               self.settings.on_description, text)  # fmt: skip
                try:
                    await self.store.add_security_events([event])
                except Exception:
                    log.exception("could not record description event for %s", tool.name)
        return found

    def _sanitize_tool(self, tool: Tool) -> Tool:
        """Replace every flagged text of the tool definition with the placeholder."""

        def clean(path: Path, value: str) -> str:
            if path and path[-1] in _SCHEMA_TEXT_KEYS and self._flagged(self.detector.score(value)):
                return PLACEHOLDER
            return value

        description = tool.description
        if description and self._flagged(self.detector.score(description)):
            description = PLACEHOLDER
        return tool.model_copy(
            update={"description": description, "input_schema": map_strings(tool.input_schema, clean)}
        )

    async def on_list_tools(self, ctx: ListContext, tools: list[Tool]) -> list[Tool]:
        """Hide or sanitize poisoned tools."""
        action = self.settings.on_description
        if action == "off":
            return tools
        kept: list[Tool] = []
        for tool in tools:
            if not self._flagged(await self._scan_tool(tool)) or action == "flag":
                kept.append(tool)
            elif action == "sanitize":
                kept.append(self._sanitize_tool(tool))
        return kept

    # ---- calls ---------------------------------------------------------------------------------
    def _record(self, ctx: CallContext, event: dict[str, Any]) -> None:
        ctx.extra.setdefault(SECURITY_EVENTS_KEY, []).append(event)

    def _raise_score(self, ctx: CallContext, score: float) -> None:
        ctx.extra["injection_score"] = max(score, ctx.extra.get("injection_score") or 0.0)

    async def on_request(self, ctx: CallContext) -> None:
        """Block calls to hidden (poisoned) tools and calls whose arguments carry an injection."""
        if ctx.tool_def is not None and self.settings.on_description == "hide":
            tool = ctx.tool_def.model_copy(update={"name": ctx.public_name})
            found = await self._scan_tool(tool)
            if self._flagged(found):
                self._raise_score(ctx, found.score)
                raise PipelineDenied("tool description flagged as prompt injection", code="tool_flagged",
                                     status="blocked")  # fmt: skip
        if self.settings.on_argument == "off":
            return
        values = [value for _, value in iter_strings(ctx.arguments)]
        if not values:
            self._raise_score(ctx, 0.0)
            return
        results = await anyio.to_thread.run_sync(self.detector.score_many, values)
        self._raise_score(ctx, max(d.score for d in results))
        flagged = [(v, d) for v, d in zip(values, results, strict=True) if self._flagged(d)]
        for value, found in flagged:
            event = _event("argument", ctx.server, ctx.tool, self.detector.name, found,
                           self.settings.on_argument, value)  # fmt: skip
            self._record(ctx, event)
        if flagged and self.settings.on_argument == "block":
            raise PipelineDenied("argument flagged as prompt injection", code="injection_argument",
                                 status="blocked")  # fmt: skip

    async def on_response(self, ctx: CallContext) -> None:
        """Score every string of the result, then sanitize, block or flag."""
        if ctx.result is None or self.settings.on_response == "off":
            return
        unique = list(dict.fromkeys(value for _, value in result_strings(ctx.result)))
        if not unique:
            return
        results = await anyio.to_thread.run_sync(self.detector.score_many, unique)
        scores = dict(zip(unique, results, strict=True))
        self._raise_score(ctx, max(d.score for d in results))
        flagged = {value: d for value, d in scores.items() if self._flagged(d)}
        if not flagged:
            return
        action = self.settings.on_response
        for value, found in flagged.items():
            self._record(
                ctx, _event("response", ctx.server, ctx.tool, self.detector.name, found, action, value)
            )
        if action == "block":
            raise PipelineDenied("tool response flagged as prompt injection", code="injection_response",
                                 status="blocked")  # fmt: skip
        if action == "sanitize":
            ctx.result, _ = map_result_strings(ctx.result, lambda _p, v: PLACEHOLDER if v in flagged else v)
            ctx.extra["injection_sanitized"] = len(flagged)
