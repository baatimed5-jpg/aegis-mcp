"""Ask the assistant one question: ``python -m aegis_agents.assistant "What is ...?"``.

Defaults: through the gateway (:8000, key from ``AGENT_TOKEN_ASSISTANT``), model from ``LLM_MODEL``.
``--direct`` talks to the wealth server (:8001) without the gateway.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from agents import Runner

from aegis_agents.assistant.agent import AssistantSettings, TaskContext, build_assistant, mcp_connection
from gateway.config import load_env_file


async def ask(settings: AssistantSettings, question: str) -> str:
    """Run the agent on one question and return its final answer."""
    async with mcp_connection(settings) as server:
        agent = build_assistant(settings, server)
        result = await Runner.run(agent, question, context=TaskContext(question=question), max_turns=12)
        return str(result.final_output)


def main() -> None:
    """Parse arguments, load ``.env`` and print the answer."""
    load_env_file(Path(__file__).resolve().parents[2] / ".env")
    parser = argparse.ArgumentParser(description="Wealth assistant (one question)")
    parser.add_argument("question")
    parser.add_argument("--direct", action="store_true", help="call the wealth server without the gateway")
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL", "gemini/gemini-3.5-flash"))
    args = parser.parse_args()
    if args.direct:
        settings = AssistantSettings(mcp_url="http://127.0.0.1:8001/mcp", model=args.model)
    else:
        settings = AssistantSettings(
            mcp_url="http://127.0.0.1:8000/mcp", model=args.model, token_env="AGENT_TOKEN_ASSISTANT"
        )
    print(asyncio.run(ask(settings, args.question)))


if __name__ == "__main__":
    main()
