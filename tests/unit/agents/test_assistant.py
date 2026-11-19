"""Unit tests: assistant settings, ``_meta`` resolver (D4) and run-id parsing in the gateway."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest

from aegis_agents.assistant.agent import (
    QUESTION_META_KEY,
    RUN_ID_META_KEY,
    AssistantSettings,
    TaskContext,
    build_model,
    mcp_connection,
    question_meta,
    record_tool_result,
)
from gateway.app import run_id_from_meta


def _meta_ctx(context: Any) -> Any:
    return cast(Any, SimpleNamespace(run_context=SimpleNamespace(context=context)))


def test_question_meta_contains_question_and_run_id() -> None:
    task = TaskContext(question="How much cash?", run_id="abc")
    assert question_meta(_meta_ctx(task)) == {QUESTION_META_KEY: "How much cash?", RUN_ID_META_KEY: "abc"}
    assert question_meta(_meta_ctx(TaskContext(question="q"))) == {QUESTION_META_KEY: "q"}
    assert question_meta(_meta_ctx(None)) is None


def test_record_tool_result_appends_to_task() -> None:
    task = TaskContext(question="q")
    ctx = cast(Any, SimpleNamespace(run_context=SimpleNamespace(context=task), tool_name="t", is_error=True))
    record_tool_result(ctx)
    assert task.tool_calls == [{"tool": "t", "is_error": True}]


def test_mcp_connection_requires_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AssistantSettings(mcp_url="http://x/mcp", model="m", token_env="AEGIS_TEST_TOKEN")
    monkeypatch.delenv("AEGIS_TEST_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="AEGIS_TEST_TOKEN"):
        mcp_connection(settings)
    monkeypatch.setenv("AEGIS_TEST_TOKEN", "secret")
    server = mcp_connection(settings)
    assert server.params["headers"] == {"Authorization": "Bearer secret"}


def test_build_model_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AssistantSettings(mcp_url="http://x/mcp", model="gemini/x", api_key_env="AEGIS_TEST_KEY")
    monkeypatch.delenv("AEGIS_TEST_KEY", raising=False)
    with pytest.raises(RuntimeError, match="AEGIS_TEST_KEY"):
        build_model(settings)


def test_run_id_from_meta() -> None:
    rid = uuid.uuid4()
    assert run_id_from_meta({"aegis/run_id": str(rid)}) == rid
    assert run_id_from_meta({"aegis/run_id": "not-a-uuid"}) is None
    assert run_id_from_meta({"aegis/run_id": 5}) is None
    assert run_id_from_meta({}) is None
