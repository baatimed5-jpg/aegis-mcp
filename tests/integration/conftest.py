"""Fixtures shared by the integration tests: a migrated test database and a wealth server process."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from gateway.config import load_env_file
from tests.integration.helpers import ROOT, free_port, spawn, stop, wait_port


@pytest.fixture(scope="session")
def test_db_url() -> str:
    """Create (if needed) and migrate a dedicated test database; return its SQLAlchemy URL."""
    load_env_file(ROOT / ".env")
    base = os.environ.get("DATABASE_URL")
    if not base:
        pytest.fail("DATABASE_URL is not set (copy .env.example to .env)")
    url = make_url(base)
    test_url = url.set(database=f"{url.database}_test")

    async def ensure_db() -> None:
        conn = await asyncpg.connect(
            user=url.username, password=url.password, host=url.host, port=url.port, database=url.database
        )
        try:
            exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", test_url.database)
            if not exists:
                await conn.execute(f'CREATE DATABASE "{test_url.database}"')
        finally:
            await conn.close()

    asyncio.run(ensure_db())
    rendered = test_url.render_as_string(hide_password=False)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["database_url"] = rendered
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    return rendered


@pytest.fixture(scope="session")
def wealth_url() -> Iterator[str]:
    port = free_port()
    proc = spawn(["-m", "servers.wealth", "--port", str(port)])
    try:
        wait_port(port, proc, "wealth server")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        stop(proc)
