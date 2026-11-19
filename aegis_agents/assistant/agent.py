"""The wealth assistant: an Agents SDK ``Agent`` whose only tools come from one MCP endpoint.

The endpoint is either the gateway (``http://127.0.0.1:8000/mcp``, tools named ``wealth__*``) or the
wealth server itself (``:8001``, no gateway). The model is reached through LiteLLM, so the provider
is a config value (``gemini/<model>`` by default).

Every ``tools/call`` carries the user question in ``_meta["aegis/question"]`` (D4) and, during
evaluation, the experiment run id in ``_meta["aegis/run_id"]``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from agents import Agent, Model, ModelSettings, set_tracing_disabled
from agents.extensions.models.litellm_model import LitellmModel
from agents.mcp import MCPServerStreamableHttp
from agents.mcp.util import MCPToolCustomDataContext, MCPToolMetaContext

QUESTION_META_KEY = "aegis/question"
RUN_ID_META_KEY = "aegis/run_id"

INSTRUCTIONS = """You are a careful assistant for wealth advisors at a private bank.
Answer questions about clients, portfolios, holdings, transactions and performance.

Rules:
- Always get facts from the tools; never guess or invent numbers, names or ids.
- Find a client by name with the client lookup tool, then use the ids it returns.
- When a question needs several steps (e.g. several portfolios), call the tools for each step.
- Report numbers exactly as the tools return them (same precision), with the currency or % sign.
- Transfers are simulations; only call the transfer tool when the user explicitly asks for one.
- If a tool returns an error or the data does not exist, say so plainly.
- Finish with a short, direct answer to the question."""


@dataclass
class TaskContext:
    """Per-question context (the Agents SDK passes it to hooks and to the ``_meta`` resolver)."""

    question: str
    run_id: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class AssistantSettings:
    """Where the assistant connects and which model it uses."""

    mcp_url: str
    model: str
    token_env: str | None = None
    """Env var holding the agent API key (gateway only); ``None`` for a direct server."""
    api_key_env: str = "GEMINI_API_KEY"
    temperature: float | None = None
    """None = provider default (Gemini 3.x models reject the parameter, D28)."""
    seed: int = 42
    tool_timeout_s: float = 150.0
    """Must exceed the gateway ``approval_timeout_s`` so approval waits do not time out the client."""


def question_meta(ctx: MCPToolMetaContext) -> dict[str, Any] | None:
    """``_meta`` sent with each tool call: the user question (D4) and the evaluation run id."""
    task = ctx.run_context.context
    if not isinstance(task, TaskContext):
        return None
    meta: dict[str, Any] = {QUESTION_META_KEY: task.question}
    if task.run_id:
        meta[RUN_ID_META_KEY] = task.run_id
    return meta


def record_tool_result(ctx: MCPToolCustomDataContext) -> None:
    """Log each tool call (name, error flag) into the ``TaskContext`` for the evaluation."""
    task = ctx.run_context.context
    if isinstance(task, TaskContext):
        task.tool_calls.append({"tool": ctx.tool_name, "is_error": bool(ctx.is_error)})


def mcp_connection(settings: AssistantSettings) -> MCPServerStreamableHttp:
    """MCP client for the configured endpoint (use as ``async with``)."""
    headers: dict[str, str] = {}
    if settings.token_env:
        token = os.environ.get(settings.token_env)
        if not token:
            raise RuntimeError(f"{settings.token_env} is not set (see .env)")
        headers["Authorization"] = f"Bearer {token}"
    return MCPServerStreamableHttp(
        params={
            "url": settings.mcp_url,
            "headers": headers,
            "timeout": settings.tool_timeout_s,
            "sse_read_timeout": settings.tool_timeout_s,
        },
        name="aegis",
        cache_tools_list=True,
        client_session_timeout_seconds=settings.tool_timeout_s,
        tool_meta_resolver=question_meta,
        custom_data_extractor=record_tool_result,
    )


def build_model(settings: AssistantSettings) -> LitellmModel:
    """LiteLLM-backed model; the API key is read from the environment, never logged."""
    api_key = os.environ.get(settings.api_key_env)
    if not api_key:
        raise RuntimeError(f"{settings.api_key_env} is not set (see .env)")
    return LitellmModel(model=settings.model, api_key=api_key)


def build_assistant(
    settings: AssistantSettings, server: MCPServerStreamableHttp, model: Model | None = None
) -> Agent[TaskContext]:
    """The assistant agent bound to one connected MCP server (``model`` overrides LiteLLM, for tests)."""
    set_tracing_disabled(True)  # no OpenAI trace export: no key, and data stays local
    return Agent[TaskContext](
        name="wealth-assistant",
        instructions=INSTRUCTIONS,
        model=model if model is not None else build_model(settings),
        model_settings=ModelSettings(temperature=settings.temperature, extra_args={"seed": settings.seed}),
        mcp_servers=[server],
    )
