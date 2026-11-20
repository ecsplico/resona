"""``resona-dictations-mcp``: serve the MCP endpoint with uvicorn. Logs go to stderr."""
import logging

import uvicorn
from decouple import config

from .http import create_app


def main() -> None:
    level = getattr(logging, config("LOGLEVEL", default="info").upper(), logging.INFO)
    # Third-party loggers never go below INFO: at DEBUG the MCP SDK and the HTTP
    # client log message bodies, which hold transcript text.
    logging.basicConfig(level=max(level, logging.INFO), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("resona_dictations_mcp").setLevel(level)
    uvicorn.run(
        create_app(),
        host=config("DICTATIONS_MCP_HOST", default="127.0.0.1"),
        port=config("DICTATIONS_MCP_PORT", default=8000, cast=int),
        log_level=max(level, logging.INFO),
    )


if __name__ == "__main__":
    main()
