"""Unit tests: PII masking with Presidio (Phase 3 DONE criterion 3)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from gateway.app import Gateway
from gateway.config import GatewayConfig
from gateway.middlewares.audit import AuditMiddleware
from gateway.middlewares.pii import PiiMaskMiddleware
from gateway.pii import PiiMasker, key_context
from gateway.pipeline.content import map_result_strings, result_strings
from gateway.pipeline.context import CallContext
from gateway.pipeline.runner import Pipeline
from gateway.store import MemoryStore
from servers.wealth.data import generate_dataset
from servers.wealth.server import build_server

CONFIG = GatewayConfig.model_validate(
    {"servers": {"wealth": {"transport": "streamable_http", "url": "http://unused/mcp"}}}
)


@pytest.fixture(scope="module")
def masker() -> PiiMasker:
    return PiiMasker()


@pytest.mark.parametrize(
    ("text", "context", "expected"),
    [
        ("james.murphy@gmail.com", ["email"], "<EMAIL_ADDRESS>"),
        ("GB46XXVG56159514846564", ["iban"], "<IBAN_CODE>"),
        ("568-17-3144", ["tax", "id"], "<US_SSN>"),
        ("469-313-7367x850", ["phone"], "<PHONE_NUMBER>"),
        ("Card 4111 1111 1111 1111 on file", None, "Card <CREDIT_CARD> on file"),
        ("Write to anna@example.org or call later", None, "Write to <EMAIL_ADDRESS> or call later"),
        # identifiers that look like PII but score below min_score stay untouched
        ("ORD-214408183", ["order", "id"], "ORD-214408183"),
        ("STL-6618965944", ["settlement", "id"], "STL-6618965944"),
        ("PF-00001", ["portfolio", "id"], "PF-00001"),
        ("no digits here", None, "no digits here"),
    ],
)
def test_mask_text(masker: PiiMasker, text: str, context: list[str] | None, expected: str) -> None:
    assert masker.mask_text(text, context)[0] == expected


def test_key_context() -> None:
    assert key_context(("structured", "clients", 0, "tax_id")) == ["tax", "id"]
    assert key_context(("content", 0)) is None


def test_wealth_client_is_masked(masker: PiiMasker) -> None:
    """Every client's email, IBAN and tax id are masked; most phone formats are too."""
    dataset = generate_dataset()
    clients = list(dataset.clients.values())
    phones_masked = 0
    for client in clients:
        data = client.model_dump(mode="json") if hasattr(client, "model_dump") else vars(client)
        masked, count = masker.mask_value(data)
        assert masked["email"] == "<EMAIL_ADDRESS>"
        assert masked["iban"] == "<IBAN_CODE>"
        assert masked["tax_id"] == "<US_SSN>"
        assert masked["client_id"] == data["client_id"]
        assert masked["name"] == data["name"]  # PERSON is not in the configured entities
        phones_masked += masked["phone"] == "<PHONE_NUMBER>"
        assert count >= 3
    # Faker phone formats like "001-345-886-8501x42940" are not recognized by Presidio (known limit).
    assert phones_masked / len(clients) >= 0.8


def _result(data: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(data, indent=2))], structured_content=data
    )


async def test_middleware_masks_both_copies_and_counts_once(masker: PiiMasker) -> None:
    data = {"client": {"email": "a.b@example.com", "iban": "GB46XXVG56159514846564", "id": "CL-1"}}
    ctx = CallContext(agent_id="a", public_name="s__t", server="s", tool="t", arguments={})
    ctx.result = _result(data)
    await PiiMaskMiddleware(masker).on_response(ctx)
    assert ctx.extra["pii_entities"] == 2
    texts = {path: value for path, value in result_strings(ctx.result)}
    assert texts[("structured", "client", "email")] == "<EMAIL_ADDRESS>"
    assert texts[("content", 0, "client", "iban")] == "<IBAN_CODE>"
    assert all("example.com" not in v for v in texts.values())


def test_map_result_strings_keeps_untouched_results() -> None:
    result = _result({"a": "x"})
    same, changed = map_result_strings(result, lambda _p, v: v)
    assert same is result
    assert not changed
    plain = CallToolResult(content=[TextContent(type="text", text="not json")])
    new, changed = map_result_strings(plain, lambda _p, v: v.upper())
    assert changed
    assert new.content[0].text == "NOT JSON"  # type: ignore[union-attr]


async def test_gateway_masks_results_and_audit_arguments(masker: PiiMasker) -> None:
    """Agent gets masked data; the downstream tool gets real arguments; the audit log gets masked ones."""
    store = MemoryStore()
    pii = PiiMaskMiddleware(masker)
    pipeline = Pipeline(request=[pii, AuditMiddleware(store)], response=[pii])
    client = next(iter(generate_dataset().clients.values()))
    async with Gateway(CONFIG, targets={"wealth": build_server()}, pipeline=pipeline) as gw:
        res = await gw.call_tool("a", "wealth__get_client", {"client_id": client.client_id})
        lookup = await gw.call_tool("a", "wealth__get_client", {"name": f"{client.email} please"})
    assert not res.is_error
    assert res.structured_content is not None
    assert res.structured_content["email"] == "<EMAIL_ADDRESS>"
    assert client.email not in res.content[0].text  # type: ignore[union-attr]
    assert client.iban not in res.content[0].text  # type: ignore[union-attr]
    rows = list(store.audit.values())
    assert rows[0]["pii_entities"] >= 3
    assert rows[1]["arguments"] == {"name": "<EMAIL_ADDRESS> please"}
    assert lookup.is_error  # the wealth server received the real (unmasked) name and found no client
