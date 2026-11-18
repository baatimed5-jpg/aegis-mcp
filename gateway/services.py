"""Objects shared by middlewares and the REST API (one store, one approval queue per gateway)."""

from __future__ import annotations

from gateway.approvals import ApprovalService
from gateway.config import GatewayConfig
from gateway.store import GatewayStore, PostgresStore


class Services:
    """Lazily created shared services; the gateway closes them on exit."""

    def __init__(self, config: GatewayConfig, store: GatewayStore | None = None) -> None:
        self.config = config
        self._store = store
        self._approvals: ApprovalService | None = None

    @property
    def has_store(self) -> bool:
        """True if a store exists or can be created (``DATABASE_URL`` is set)."""
        return self._store is not None or bool(self.config.database_url)

    @property
    def store(self) -> GatewayStore:
        """The gateway store (PostgreSQL unless one was injected)."""
        if self._store is None:
            if not self.config.database_url:
                raise ValueError("this configuration needs DATABASE_URL (env or config)")
            self._store = PostgresStore.from_url(self.config.database_url)
        return self._store

    @property
    def approvals(self) -> ApprovalService:
        """The approval queue."""
        if self._approvals is None:
            self._approvals = ApprovalService(self.store, timeout_s=self.config.approval_timeout_s)
        return self._approvals

    async def close(self) -> None:
        """Close the store if one was created."""
        if self._store is not None:
            await self._store.close()
