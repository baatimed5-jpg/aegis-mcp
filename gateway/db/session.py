"""Async database engine and session factory."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def make_engine(database_url: str) -> AsyncEngine:
    """Create an async engine (``postgresql+asyncpg://...``) with connection health checks."""
    return create_async_engine(database_url, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory that keeps objects usable after commit."""
    return async_sessionmaker(engine, expire_on_commit=False)
