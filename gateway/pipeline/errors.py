"""Pipeline exceptions (D2: denials are exceptions that become MCP tool errors)."""

from __future__ import annotations

from typing import Literal

DenyStatus = Literal["denied", "blocked", "timeout"]


class PipelineDenied(Exception):  # noqa: N818 - the name states the outcome, as in the design docs
    """Raised by a middleware to stop a call. The agent receives ``isError=true`` with ``reason``."""

    def __init__(self, reason: str, code: str = "denied", status: DenyStatus = "denied") -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code
        self.status: DenyStatus = status


class UnknownToolError(Exception):
    """The requested public tool name does not map to a known downstream tool."""
