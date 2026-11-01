import copy
import logging
import re

import uvicorn
from decouple import config
from uvicorn.config import LOGGING_CONFIG

from .app import app


loglevel = config("LOGLEVEL", default="info")

_QUERY = re.compile(r"\?[^\s\"']*")


class RedactQueryStrings(logging.Filter):
    """Drop query strings from uvicorn's access and connection log lines.

    Tokens may travel as ``?token=``/``?api_key=`` on ``WS /v1/listen``; uvicorn
    logs the full request target, so every string argument loses its query part.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _QUERY.sub("?<redacted>", arg) if isinstance(arg, str) else arg for arg in record.args
            )
        elif isinstance(record.msg, str) and not record.args:
            record.msg = _QUERY.sub("?<redacted>", record.msg)
        return True


def log_config() -> dict:
    """uvicorn's default logging config with :class:`RedactQueryStrings` on every handler."""
    cfg = copy.deepcopy(LOGGING_CONFIG)
    cfg.setdefault("filters", {})["redact_query"] = {"()": f"{__name__}.RedactQueryStrings"}
    for handler in cfg["handlers"].values():
        handler.setdefault("filters", []).append("redact_query")
    return cfg


def main():
    """Entry point for the resona-api command."""
    uvicorn.run(app, host="0.0.0.0", port=7000, log_level=loglevel, log_config=log_config())


if __name__ == "__main__":
    main()
