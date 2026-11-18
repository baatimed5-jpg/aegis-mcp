"""Unit tests: tool namespacing and the config loader."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from gateway.config import GatewayConfig, load_config, load_env_file
from gateway.namespacing import namespace, split

REPO_CONFIG = Path(__file__).resolve().parents[3] / "config" / "gateway.yaml"


def test_namespace_round_trip() -> None:
    assert namespace("wealth", "get_holdings") == "wealth__get_holdings"
    assert split("wealth__get_holdings") == ("wealth", "get_holdings")


def test_split_keeps_double_underscores_in_tool_name() -> None:
    assert split("srv__my__tool") == ("srv", "my__tool")


@pytest.mark.parametrize("name", ["get_holdings", "__tool", "wealth__", ""])
def test_split_rejects_non_namespaced(name: str) -> None:
    with pytest.raises(ValueError, match="not a namespaced"):
        split(name)


@pytest.mark.parametrize(("server", "tool"), [("", "t"), ("a__b", "t"), ("s", "")])
def test_namespace_rejects_bad_parts(server: str, tool: str) -> None:
    with pytest.raises(ValueError, match=r"invalid|empty"):
        namespace(server, tool)


def test_repo_config_loads() -> None:
    config = load_config(REPO_CONFIG)
    assert config.servers["wealth"].transport == "streamable_http"
    assert config.pipeline.request == ["auth", "pii_mask", "policy", "injection_scan", "audit"]
    assert config.pipeline.response == ["pii_mask", "injection_scan"]
    assert "wealth" in config.enabled_servers
    assert "malicious" not in config.enabled_servers  # test-only server stays off by default
    assert config.policies_file is not None
    assert Path(config.policies_file).is_file()


def test_database_url_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:1/db")
    cfg = tmp_path / "g.yaml"
    cfg.write_text("servers: {s: {transport: streamable_http, url: 'http://x/mcp'}}\n", encoding="utf-8")
    assert load_config(cfg).database_url == "postgresql+asyncpg://u:p@h:1/db"


def test_env_file_does_not_override_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AEGIS_TEST_A", "from-env")
    monkeypatch.delenv("AEGIS_TEST_B", raising=False)
    env = tmp_path / ".env"
    env.write_text("# comment\nAEGIS_TEST_A=from-file\nAEGIS_TEST_B='quoted'\nnot a pair\n", encoding="utf-8")
    load_env_file(env)
    import os

    assert os.environ["AEGIS_TEST_A"] == "from-env"
    assert os.environ["AEGIS_TEST_B"] == "quoted"
    monkeypatch.delenv("AEGIS_TEST_B")


@pytest.mark.parametrize(
    "servers",
    [
        {"a__b": {"transport": "stdio", "command": "x"}},
        {"s": {"transport": "streamable_http"}},
        {"s": {"transport": "stdio"}},
        {"s": {"transport": "stdio", "command": "x", "typo": 1}},
    ],
)
def test_invalid_server_configs(servers: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        GatewayConfig.model_validate({"servers": servers})


def test_disabled_servers_are_skipped() -> None:
    cfg = GatewayConfig.model_validate(
        {
            "servers": {
                "a": {"transport": "stdio", "command": "x"},
                "b": {"transport": "stdio", "command": "x", "enabled": False},
            }
        }
    )
    assert list(cfg.enabled_servers) == ["a"]
