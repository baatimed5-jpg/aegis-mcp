"""Tool namespacing: tool ``get_holdings`` on server ``wealth`` becomes ``wealth__get_holdings``."""

from __future__ import annotations

SEPARATOR = "__"


def namespace(server: str, tool: str) -> str:
    """Return the public (agent-facing) name of a downstream tool."""
    if not server or SEPARATOR in server:
        raise ValueError(f"invalid server name: {server!r}")
    if not tool:
        raise ValueError("tool name must not be empty")
    return f"{server}{SEPARATOR}{tool}"


def split(name: str) -> tuple[str, str]:
    """Split a public tool name into ``(server, tool)``; the first separator wins.

    Raises ``ValueError`` when the name has no separator or an empty part.
    """
    server, sep, tool = name.partition(SEPARATOR)
    if not sep or not server or not tool:
        raise ValueError(f"not a namespaced tool name: {name!r}")
    return server, tool
