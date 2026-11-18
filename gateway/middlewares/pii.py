"""PII masking middleware: masks tool results for the agent and arguments for the audit log."""

from __future__ import annotations

import anyio

from gateway.pii import PiiMasker, key_context
from gateway.pipeline.base import Middleware
from gateway.pipeline.content import Path, map_result_strings
from gateway.pipeline.context import CallContext


class PiiMaskMiddleware(Middleware):
    """Request hook: store a masked copy of the arguments in ``ctx.audit_arguments`` (the downstream
    tool still gets the real values). Response hook: mask ``ctx.result`` (structured and text copies)
    and count findings in ``ctx.extra["pii_entities"]``.

    Presidio is CPU-bound and synchronous, so it runs in a worker thread.
    """

    name = "pii_mask"

    def __init__(self, masker: PiiMasker | None = None, **masker_args: object) -> None:
        """Use ``masker``, or build one at startup from ``masker_args`` (loading spaCy is slow)."""
        self.masker = masker
        self._masker_args = masker_args

    async def startup(self) -> None:
        """Load the spaCy model and Presidio recognizers."""
        if self.masker is None:
            args = self._masker_args
            self.masker = await anyio.to_thread.run_sync(lambda: PiiMasker(**args))  # type: ignore[arg-type]

    def _require(self) -> PiiMasker:
        if self.masker is None:
            raise RuntimeError("PiiMaskMiddleware used before startup()")
        return self.masker

    async def on_request(self, ctx: CallContext) -> None:
        """Mask the arguments kept for audit and approvals."""
        masker = self._require()
        masked, count = await anyio.to_thread.run_sync(masker.mask_value, ctx.arguments)
        ctx.audit_arguments = masked
        ctx.extra["pii_entities_arguments"] = count

    async def on_response(self, ctx: CallContext) -> None:
        """Mask the result returned to the agent."""
        if ctx.result is None:
            return
        masker = self._require()
        result = ctx.result
        # The structured copy and the text copy hold the same data: count findings in one of them.
        counted = "structured" if result.structured_content is not None else "content"
        total = 0

        def mask(path: Path, text: str) -> str:
            nonlocal total
            masked, count = masker.mask_text(text, key_context(path))
            if path[0] == counted:
                total += count
            return masked

        ctx.result, _ = await anyio.to_thread.run_sync(lambda: map_result_strings(result, mask))
        ctx.extra["pii_entities"] = total
