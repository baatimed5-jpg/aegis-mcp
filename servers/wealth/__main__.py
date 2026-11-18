"""Run the wealth MCP server: ``python -m servers.wealth [--stdio] [--host H] [--port P]``."""

from __future__ import annotations

import argparse

from servers.wealth.server import build_server


def main() -> None:
    """Parse CLI arguments and start the server (Streamable HTTP by default)."""
    parser = argparse.ArgumentParser(description="Fictional wealth-management MCP server")
    parser.add_argument("--stdio", action="store_true", help="use stdio instead of Streamable HTTP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    server = build_server()
    if args.stdio:
        server.run("stdio")
    else:
        server.run("streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
