"""Build middlewares from their config names (``pipeline.request`` / ``pipeline.response``)."""

from __future__ import annotations

from collections.abc import Callable

from gateway.config import GatewayConfig
from gateway.middlewares.audit import AuditMiddleware, PostgresAuditStore
from gateway.pipeline.base import Middleware
from gateway.pipeline.runner import Pipeline

Factory = Callable[[GatewayConfig], Middleware]


def _audit(config: GatewayConfig) -> Middleware:
    if not config.database_url:
        raise ValueError("the 'audit' middleware needs DATABASE_URL (env or config)")
    return AuditMiddleware(PostgresAuditStore.from_url(config.database_url))


FACTORIES: dict[str, Factory] = {"audit": _audit}


def build_pipeline(config: GatewayConfig, factories: dict[str, Factory] | None = None) -> Pipeline:
    """Create one instance per middleware name, shared between the request and response lists."""
    table = FACTORIES if factories is None else factories
    instances: dict[str, Middleware] = {}
    for name in [*config.pipeline.request, *config.pipeline.response]:
        if name in instances:
            continue
        if name not in table:
            raise ValueError(f"unknown middleware {name!r}; available: {sorted(table)}")
        instances[name] = table[name](config)
    return Pipeline(
        request=[instances[n] for n in config.pipeline.request],
        response=[instances[n] for n in config.pipeline.response],
    )
