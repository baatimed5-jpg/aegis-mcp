"""Helpers shared by the integration tests (subprocesses, ports, database connections)."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    """A TCP port that is free right now."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def wait_port(port: int, proc: subprocess.Popen[bytes], what: str, timeout_s: float = 60) -> None:
    """Wait until ``port`` accepts connections; fail if the process dies first."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"{what} exited early with code {proc.returncode}")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return
        except OSError:
            time.sleep(0.2)
    pytest.fail(f"{what} did not start within {timeout_s:g} s")


def spawn(args: list[str], env: dict[str, str] | None = None) -> subprocess.Popen[bytes]:
    """Start ``python <args>`` in the repo root, output discarded."""
    return subprocess.Popen(
        [sys.executable, *args], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def stop(proc: subprocess.Popen[bytes]) -> None:
    """Terminate a subprocess and wait for it."""
    proc.terminate()
    proc.wait(timeout=10)


async def connect(db_url: str) -> asyncpg.Connection:
    """Plain asyncpg connection from a SQLAlchemy URL."""
    url = make_url(db_url)
    return await asyncpg.connect(
        user=url.username, password=url.password, host=url.host, port=url.port, database=url.database
    )
