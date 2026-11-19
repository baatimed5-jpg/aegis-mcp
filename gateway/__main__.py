"""Run the gateway: ``python -m gateway [--stdio --agent ID] [--config PATH] [--host H] [--port P]``."""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

import anyio
import uvicorn

from gateway.app import Gateway, create_http_app, run_stdio
from gateway.config import DEFAULT_CONFIG_PATH, load_config

ROOT = Path(__file__).resolve().parents[1]
_TOKEN = re.compile(r"(token=)[^&\s\"']+")


class RedactTokens(logging.Filter):
    """Mask ``token=...`` query values (dashboard WebSocket key) in server logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _TOKEN.sub("token=***", str(record.msg))
        if record.args:
            record.args = tuple(_TOKEN.sub("token=***", a) if isinstance(a, str) else a for a in record.args)
        return True


def redact_server_logs() -> None:
    """Attach the redaction filter to uvicorn's loggers (access lines carry the request path)."""
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn"):
        logging.getLogger(name).addFilter(RedactTokens())


def main() -> None:
    """Parse CLI arguments and start the gateway (Streamable HTTP by default)."""
    parser = argparse.ArgumentParser(description="AegisMCP gateway")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--stdio", action="store_true", help="serve one agent over stdio")
    parser.add_argument("--agent", default=None, help="agent id (stdio mode)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    # stdout is the MCP channel in stdio mode, so logs always go to stderr.
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    config = load_config(args.config, env_file=ROOT / ".env")
    gateway = Gateway(config)
    if args.stdio:
        anyio.run(run_stdio, gateway, args.agent or config.default_agent)
    else:
        redact_server_logs()
        log_config = uvicorn.config.LOGGING_CONFIG
        log_config["filters"] = {"redact": {"()": "gateway.__main__.RedactTokens"}}
        for handler in log_config["handlers"].values():
            handler["filters"] = ["redact"]
        uvicorn.run(
            create_http_app(gateway, host=args.host), host=args.host, port=args.port, log_config=log_config
        )


if __name__ == "__main__":
    main()
