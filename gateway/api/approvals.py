"""Admin REST API for the approval queue (``/api/approvals``).

Every route needs ``Authorization: Bearer <admin key>``; the key comes from the env var named by
``admin_token_env`` (default ``ADMIN_TOKEN``). Without that variable the API answers 503, so an
unconfigured gateway never accepts anonymous approvals.
"""

from __future__ import annotations

import hmac
import os
import uuid
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from gateway.approvals import ApprovalNotFound

if TYPE_CHECKING:
    from gateway.app import Gateway


class Decision(BaseModel):
    """Body of ``POST /api/approvals/{id}``."""

    decision: Literal["approve", "deny"]
    decided_by: str = Field(default="admin", min_length=1, max_length=100)


def approvals_router(gateway: Gateway) -> APIRouter:
    """Routes to list pending approvals and decide them."""
    router = APIRouter(prefix="/api/approvals", tags=["approvals"])

    def require_admin(authorization: Annotated[str | None, Header()] = None) -> None:
        env = gateway.config.admin_token_env
        expected = os.environ.get(env) if env else None
        if not expected:
            raise HTTPException(status_code=503, detail="admin API disabled: no admin key configured")
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip().encode(), expected.encode()):
            raise HTTPException(status_code=401, detail="invalid admin key")

    admin = [Depends(require_admin)]

    @router.get("", dependencies=admin)
    async def list_pending() -> list[dict[str, Any]]:
        """Approvals waiting for a decision, oldest first."""
        return [item.public() for item in gateway.services.approvals.pending()]

    @router.post("/{approval_id}", dependencies=admin)
    async def decide(approval_id: uuid.UUID, body: Decision) -> dict[str, Any]:
        """Approve or deny one pending call."""
        try:
            item = await gateway.services.approvals.decide(
                approval_id, approve=body.decision == "approve", decided_by=body.decided_by
            )
        except ApprovalNotFound:
            raise HTTPException(status_code=404, detail="no pending approval with this id") from None
        return item.public()

    return router
