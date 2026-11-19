"""One LiteLLM chat call with simple retries (LiteLLM's own ``num_retries`` needs ``tenacity``).

Retries rate limits (per-minute), overload and connection errors with a growing pause; a daily
quota or a bad key is raised at once (waiting would not help).
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

RETRYABLE = ("ratelimit", "429", "503", "overloaded", "high demand", "unavailable", "connection", "timeout")
FATAL = ("perday", "api key not valid", "notfound", "permission")


def _retryable(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return not any(f in text for f in FATAL) and any(r in text for r in RETRYABLE)


def _kwargs(model: str, prompt: str, api_key_env: str) -> dict[str, Any]:
    return {
        "model": model,
        "api_key": os.environ.get(api_key_env, ""),
        "messages": [{"role": "user", "content": prompt}],
        "seed": 42,
    }


def complete(model: str, prompt: str, *, api_key_env: str = "GEMINI_API_KEY", attempts: int = 5) -> str:
    """Text reply of the model (synchronous)."""
    import litellm

    for attempt in range(attempts):
        try:
            resp = litellm.completion(**_kwargs(model, prompt, api_key_env))
            return str(resp.choices[0].message.content or "")
        except Exception as exc:
            if attempt == attempts - 1 or not _retryable(exc):
                raise
            time.sleep(15 * (attempt + 1))
    raise AssertionError("unreachable")


async def acomplete(
    model: str, prompt: str, *, api_key_env: str = "GEMINI_API_KEY", attempts: int = 5
) -> str:
    """Text reply of the model (async)."""
    import litellm

    for attempt in range(attempts):
        try:
            resp = await litellm.acompletion(**_kwargs(model, prompt, api_key_env))
            return str(resp.choices[0].message.content or "")
        except Exception as exc:
            if attempt == attempts - 1 or not _retryable(exc):
                raise
            await asyncio.sleep(15 * (attempt + 1))
    raise AssertionError("unreachable")
