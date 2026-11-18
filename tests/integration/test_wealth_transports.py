"""Integration tests: the wealth server works over its real transports (subprocess)."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@pytest.fixture(scope="module")
def http_url() -> Iterator[str]:
    """Start ``python -m servers.wealth`` on a free port and wait until it accepts connections."""
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "servers.wealth", "--port", str(port)],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.2)
        else:
            pytest.fail("wealth server did not start within 30 s")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


async def test_streamable_http(http_url: str) -> None:
    async with Client(http_url) as client:
        tools = (await client.list_tools()).tools
        res = await client.call_tool(
            "get_portfolio_performance", {"portfolio_id": "PF-00001", "period": "3M"}
        )
    assert len(tools) == 6
    assert not res.is_error
    assert isinstance(res.structured_content, dict)
    assert len(res.structured_content["monthly"]) == 3


async def test_stdio() -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "servers.wealth", "--stdio"], cwd=ROOT)
    async with Client(params) as client:
        res = await client.call_tool("list_portfolios", {"client_id": "CL-0001"})
    assert not res.is_error
    assert isinstance(res.structured_content, dict)
    assert res.structured_content["client_id"] == "CL-0001"
