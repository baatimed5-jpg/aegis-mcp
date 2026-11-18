"""Access policies: per-agent allow lists (``config/policies.yaml``).

Format (chosen with the user, D22)::

    agents:
      assistant:
        allow: ["wealth__*"]
        require_approval: [wealth__transfer_funds]

Patterns are shell-style globs on public tool names (``fnmatch``, case-sensitive).
``require_approval`` wins over ``allow``; anything not matched, and any agent not listed, is denied.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

Decision = Literal["allow", "approval", "deny"]


class AgentPolicy(BaseModel):
    """Tools one agent may call."""

    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)
    require_approval: list[str] = Field(default_factory=list)


class PolicySet(BaseModel):
    """All agents' policies."""

    model_config = ConfigDict(extra="forbid")

    agents: dict[str, AgentPolicy] = Field(default_factory=dict)

    def decide(self, agent_id: str, tool: str) -> Decision:
        """Return ``approval``, ``allow`` or ``deny`` for ``agent_id`` calling public tool ``tool``."""
        policy = self.agents.get(agent_id)
        if policy is None:
            return "deny"
        if any(fnmatchcase(tool, pattern) for pattern in policy.require_approval):
            return "approval"
        if any(fnmatchcase(tool, pattern) for pattern in policy.allow):
            return "allow"
        return "deny"


def load_policies(path: Path | str) -> PolicySet:
    """Read and validate a policy file."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return PolicySet.model_validate(data)
