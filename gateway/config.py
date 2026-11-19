"""Gateway configuration: YAML file (``config/gateway.yaml``) + environment variables.

Secrets (database URL, agent tokens) never live in the YAML file; they come from the
environment, optionally pre-loaded from a local ``.env`` file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from gateway.namespacing import SEPARATOR

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "gateway.yaml"


class _Strict(BaseModel):
    """Base model that rejects unknown keys, so config typos fail loudly."""

    model_config = ConfigDict(extra="forbid")


class ServerConfig(_Strict):
    """One downstream MCP server."""

    transport: Literal["streamable_http", "stdio"]
    url: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    enabled: bool = True
    timeout_s: float = 60.0

    @model_validator(mode="after")
    def _check_transport_fields(self) -> Self:
        if self.transport == "streamable_http" and not self.url:
            raise ValueError("streamable_http servers need a 'url'")
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio servers need a 'command'")
        return self


class AgentConfig(_Strict):
    """An agent allowed to use the gateway; its API key is read from the env var ``token_env`` (D5)."""

    token_env: str | None = None


class PipelineConfig(_Strict):
    """Middleware order for requests and responses (D1: two independent lists)."""

    request: list[str] = Field(default_factory=list)
    response: list[str] = Field(default_factory=list)


class CacheConfig(_Strict):
    """Downstream result cache (D3)."""

    enabled: bool = False
    ttl_s: int = Field(default=300, gt=0)


class GatewayConfig(_Strict):
    """Full gateway configuration."""

    servers: dict[str, ServerConfig]
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    middlewares: dict[str, dict[str, Any]] = Field(default_factory=dict)
    default_agent: str = "anonymous"
    database_url: str | None = None
    policies_file: str | None = None
    approval_timeout_s: float = Field(default=120.0, gt=0)
    admin_token_env: str | None = "ADMIN_TOKEN"
    cache: CacheConfig = Field(default_factory=CacheConfig)
    redis_url: str | None = None
    """Taken from ``REDIS_URL`` when unset; without it, cache and evidence stores are in memory."""
    config_dir: str | None = None
    """Directory of the loaded YAML (set by ``load_config``) to resolve relative file names."""
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:4200"])

    @field_validator("servers")
    @classmethod
    def _check_server_names(cls, servers: dict[str, ServerConfig]) -> dict[str, ServerConfig]:
        for name in servers:
            if not name or SEPARATOR in name:
                raise ValueError(
                    f"invalid server name {name!r}: must be non-empty and not contain {SEPARATOR!r}"
                )
        return servers

    @property
    def enabled_servers(self) -> dict[str, ServerConfig]:
        """Servers with ``enabled: true``."""
        return {name: s for name, s in self.servers.items() if s.enabled}


def load_env_file(path: Path) -> None:
    """Load ``KEY=VALUE`` lines from a ``.env`` file into ``os.environ`` (existing vars win)."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def load_config(path: Path | str = DEFAULT_CONFIG_PATH, *, env_file: Path | None = None) -> GatewayConfig:
    """Read the YAML config, then take ``DATABASE_URL`` from the environment.

    A relative ``policies_file`` is resolved against the YAML file's directory.
    """
    if env_file is not None:
        load_env_file(env_file)
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    config = GatewayConfig.model_validate(data)
    if config.database_url is None:
        config.database_url = os.environ.get("DATABASE_URL")
    if config.redis_url is None:
        config.redis_url = os.environ.get("REDIS_URL") or None
    config.config_dir = str(Path(path).resolve().parent)
    if config.policies_file and not Path(config.policies_file).is_absolute():
        config.policies_file = str((Path(path).parent / config.policies_file).resolve())
    return config
