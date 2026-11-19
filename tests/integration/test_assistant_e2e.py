"""Integration test: the assistant + evaluation runner through a real gateway, without an LLM.

A scripted ``Model`` replaces Gemini: it asks for fixed tool calls, then writes its answer from the
tool outputs it received, so the answer is right only if the gateway really returned the data.
Proves (Phase 4): the Agents SDK reaches gateway tools over Streamable HTTP with the agent key,
``_meta`` travels with each call (the audit row carries the experiment run id sent in ``_meta``,
D4), the approval queue + auto-approver work for transfers, and ``run_task`` grades the answers.

Needs ``docker compose up -d postgres``.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from agents import Model, ModelResponse, Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

from aegis_agents.assistant.agent import AssistantSettings, build_assistant, mcp_connection
from evaluation.runner import auto_approver, run_task, tool_output_text
from servers.wealth.data import generate_dataset
from servers.wealth.server import WealthService
from tests.integration.helpers import ROOT, connect, free_port, spawn, stop, wait_port

pytestmark = pytest.mark.integration

KEYS = {"AGENT_TOKEN_ASSISTANT": secrets.token_urlsafe(24), "ADMIN_TOKEN": secrets.token_urlsafe(24)}
Step = tuple[str, dict[str, Any]]


class ScriptedModel(Model):
    """Returns the scripted tool calls one per turn, then ``answer(tool_outputs)`` as the final text."""

    def __init__(self, steps: list[Step], answer: Callable[[list[dict[str, Any]]], str]) -> None:
        self.steps = steps
        self.answer = answer

    async def get_response(
        self, system_instructions: Any, input: Any, *args: Any, **kwargs: Any
    ) -> ModelResponse:
        items = input if isinstance(input, list) else []
        outputs = [
            json.loads(tool_output_text(i["output"]))
            for i in items
            if isinstance(i, dict) and i.get("type") == "function_call_output"
        ]
        usage = Usage(requests=1, input_tokens=100, output_tokens=10, total_tokens=110)
        if len(outputs) < len(self.steps):
            name, tool_args = self.steps[len(outputs)]
            call = ResponseFunctionToolCall(
                type="function_call", call_id=f"c{len(outputs)}", name=name, arguments=json.dumps(tool_args),
                id=f"fc{len(outputs)}", status="completed",
            )  # fmt: skip
            return ModelResponse(output=[call], usage=usage, response_id=None)
        text = ResponseOutputText(type="output_text", text=self.answer(outputs), annotations=[])
        msg = ResponseOutputMessage(
            id="m1", type="message", role="assistant", status="completed", content=[text]
        )
        return ModelResponse(output=[msg], usage=usage, response_id=None)

    def stream_response(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        raise NotImplementedError


@pytest.fixture(scope="module")
def assistant_gateway(
    tmp_path_factory: pytest.TempPathFactory, wealth_url: str, test_db_url: str
) -> Iterator[str]:
    """Gateway with the repo's policies (transfers need approval) in front of the wealth server."""
    path = tmp_path_factory.mktemp("asst") / "gateway.yaml"
    policies = (ROOT / "config" / "policies.yaml").as_posix()
    path.write_text(
        f"""servers:
  wealth: {{transport: streamable_http, url: '{wealth_url}'}}
agents:
  assistant: {{token_env: AGENT_TOKEN_ASSISTANT}}
policies_file: '{policies}'
approval_timeout_s: 20
admin_token_env: ADMIN_TOKEN
pipeline:
  request: [auth, pii_mask, policy, injection_scan, audit]
  response: [pii_mask, injection_scan]
""",
        encoding="utf-8",
    )
    port = free_port()
    env = {**os.environ, "DATABASE_URL": test_db_url, **KEYS}
    proc = spawn(["-m", "gateway", "--config", str(path), "--port", str(port)], env)
    try:
        wait_port(port, proc, "gateway")
        yield f"http://127.0.0.1:{port}"
    finally:
        stop(proc)


async def _new_run(db_url: str) -> str:
    run_id = uuid.uuid4()
    conn = await connect(db_url)
    try:
        await conn.execute(
            "INSERT INTO experiment_run (id, config_name, run_index, started_at) VALUES ($1, 'test', 0, $2)",
            run_id,
            datetime.now(UTC),
        )
    finally:
        await conn.close()
    return str(run_id)


async def test_assistant_tasks_through_gateway(
    assistant_gateway: str, test_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for k, v in KEYS.items():
        monkeypatch.setenv(k, v)
    svc = WealthService(generate_dataset())
    client = next(c for c in svc.ds.clients.values() if len(c.portfolio_ids) >= 2)
    src, dst = client.portfolio_ids[:2]
    run_id = await _new_run(test_db_url)
    settings = AssistantSettings(
        mcp_url=f"{assistant_gateway}/mcp",
        model="unused",
        token_env="AGENT_TOKEN_ASSISTANT",
        tool_timeout_s=30,
    )
    risk_task = {
        "id": "X1", "category": "client_risk_profile", "question": f"Risk profile of {client.client_id}?",
        "check": {"type": "text", "all_of": [[client.risk_profile]]},
    }  # fmt: skip
    pf_src = svc.ds.portfolios[src]
    amount = 10.0
    transfer_task = {
        "id": "X2", "category": "transfer_simulation", "question": f"Simulate {amount} from {src} to {dst}.",
        "check": {"type": "number", "value": round(pf_src.cash_balance - amount, 2), "rel_tol": 0.0001},
    }  # fmt: skip
    risk_model = ScriptedModel(
        [("wealth__get_client", {"client_id": client.client_id})],
        lambda outs: f"The risk profile is {outs[0]['risk_profile']}.",
    )
    transfer_model = ScriptedModel(
        [("wealth__transfer_funds", {"from_portfolio_id": src, "to_portfolio_id": dst, "amount": amount})],
        lambda outs: f"{outs[0].get('from_cash_after', outs[0])} would remain.",
    )
    stop_event = asyncio.Event()
    approver = asyncio.create_task(
        auto_approver(f"{assistant_gateway}/api/approvals", KEYS["ADMIN_TOKEN"], stop_event)
    )
    try:
        async with mcp_connection(settings) as server:
            rows = []
            for task, model in ((risk_task, risk_model), (transfer_task, transfer_model)):
                agent = build_assistant(settings, server, model=model)
                rows.append(
                    await run_task(
                        agent,
                        task,
                        profile="test",
                        run_index=0,
                        run_id=run_id,
                        max_turns=4,
                        min_interval_s=0.0,
                        judge=None,
                    )
                )
    finally:
        stop_event.set()
        await approver

    for row in rows:
        assert row.error == "", row.error
        assert row.correct, row.answer
        assert row.tool_calls == 1
        assert row.tool_errors == 0
        assert row.tool_output_tokens > 0
        assert row.input_tokens == 200  # two scripted LLM requests
    conn = await connect(test_db_url)
    try:
        audit = await conn.fetch(
            "SELECT tool, status FROM audit_call WHERE run_id = $1 ORDER BY ts", uuid.UUID(run_id)
        )
        approvals = await conn.fetch(
            "SELECT a.status, a.decided_by FROM approval a JOIN audit_call c ON c.id = a.audit_call_id "
            "WHERE c.run_id = $1",
            uuid.UUID(run_id),
        )
    finally:
        await conn.close()
    assert [(r["tool"], r["status"]) for r in audit] == [
        ("get_client", "allowed"),
        ("transfer_funds", "approved"),
    ]
    assert [(r["status"], r["decided_by"]) for r in approvals] == [("approved", "eval-auto")]
