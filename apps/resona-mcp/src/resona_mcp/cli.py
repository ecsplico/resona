"""``resona-mcp stdio`` | ``resona-mcp http --host H --port P``."""
import argparse
import logging
import sys

import anyio


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="resona-mcp", description="Resona MCP server")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("stdio", help="serve one organization over stdio (RESONA_API_URL/RESONA_API_KEY)")
    http = sub.add_parser("http", help="serve Streamable HTTP at /mcp; every request brings its own bearer token")
    http.add_argument("--host", default="127.0.0.1")
    http.add_argument("--port", type=int, default=7010)
    args = parser.parse_args(argv)

    # Logs go to stderr only; stdout is the stdio protocol channel.
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.command == "stdio":
        from .stdio import StdioConfigError, create_stdio_server

        try:
            server = create_stdio_server()
        except StdioConfigError as exc:
            parser.exit(2, f"resona-mcp: {exc}\n")
        anyio.run(server.run_stdio_async)
        return

    import uvicorn

    from .http import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
