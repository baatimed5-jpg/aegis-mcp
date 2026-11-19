"""Rule-based field pruning (``config/pruning_rules.yaml``).

A rule is a dotted path of fields to drop from a tool's structured result. ``[]`` means "every item
of this list": ``holdings[].instrument`` drops ``instrument`` from each holding. Paths that do not
exist are ignored, so rules stay valid when a server adds or removes fields.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


class PruningRules(BaseModel):
    """Per downstream tool name (without server prefix): field paths to drop."""

    model_config = ConfigDict(extra="forbid")

    tools: dict[str, list[str]] = Field(default_factory=dict)

    def for_tool(self, tool: str) -> list[str]:
        """Drop paths for one tool (empty list: nothing to prune)."""
        return self.tools.get(tool, [])


def load_rules(path: Path | str) -> PruningRules:
    """Read a pruning-rules YAML file."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return PruningRules.model_validate(data)


def _split(path: str) -> list[str]:
    """``"a[].b.c"`` -> ``["a", "[]", "b", "c"]``."""
    parts: list[str] = []
    for chunk in path.split("."):
        if chunk.endswith("[]"):
            parts += [chunk[:-2], "[]"]
        else:
            parts.append(chunk)
    return [p for p in parts if p]


def _drop(obj: Any, parts: list[str]) -> int:
    """Remove the path from ``obj`` in place; return how many values were removed."""
    if not parts:
        return 0
    head, rest = parts[0], parts[1:]
    if head == "[]":
        if not isinstance(obj, list):
            return 0
        return sum(_drop(item, rest) for item in obj)
    if not isinstance(obj, dict) or head not in obj:
        return 0
    if not rest:
        del obj[head]
        return 1
    return _drop(obj[head], rest)


def prune(data: dict[str, Any], drop_paths: list[str]) -> tuple[dict[str, Any], list[str]]:
    """Copy of ``data`` without the listed paths, and the paths that actually removed something."""
    pruned = copy.deepcopy(data)
    removed = [path for path in drop_paths if _drop(pruned, _split(path)) > 0]
    return pruned, removed
