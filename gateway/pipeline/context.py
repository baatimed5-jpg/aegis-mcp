"""Per-call state shared by all middlewares."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from mcp.types import CallToolResult

CallStatus = Literal["in_progress", "allowed", "denied", "blocked", "error", "approved", "timeout"]


@dataclass
class CallContext:
    """One ``tools/call`` going through the gateway.

    Middlewares read and update it; the runner fills ``result``, ``status`` and latencies.
    """

    agent_id: str
    public_name: str
    server: str
    tool: str
    arguments: dict[str, Any]
    meta: dict[str, Any] = field(default_factory=dict)
    call_id: uuid.UUID = field(default_factory=uuid.uuid4)
    run_id: uuid.UUID | None = None
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))
    started: float = field(default_factory=time.perf_counter)
    status: CallStatus = "in_progress"
    reason: str | None = None
    result: CallToolResult | None = None
    latency_total_ms: float | None = None
    latency_downstream_ms: float | None = None
    cache_hit: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ListContext:
    """One ``tools/list`` going through the gateway."""

    agent_id: str
    meta: dict[str, Any] = field(default_factory=dict)
