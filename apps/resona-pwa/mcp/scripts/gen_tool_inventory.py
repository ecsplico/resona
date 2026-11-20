"""Write (or check) ``tool-inventory.json``: the MCP tool surface this service ships.

Same format as resona-mcp's inventory: a JSON array of ``{"name": str, "deferred":
bool}``, sorted by name, no duplicates. Names come from the server's real
``tools/list`` answer (an in-process MCP client against ``build_server``),
cross-checked against ``resona_dictations_mcp.server.TOOL_NAMES``; every tool must
publish an object input schema. All tools are eager (``deferred: false``).

    uv run python scripts/gen_tool_inventory.py            # rewrite tool-inventory.json
    uv run python scripts/gen_tool_inventory.py --check    # exit 1 if it is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import anyio
from mcp import Client

from resona_dictations_mcp.server import TOOL_NAMES, build_server

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "tool-inventory.json"


def _no_caller(ctx):  # pragma: no cover - listing tools never reaches Directus
    raise RuntimeError("tool listing must not identify a caller")


def inventory() -> list[dict]:
    server = build_server(_no_caller)

    async def listed():
        async with Client(server) as client:
            return (await client.list_tools()).tools

    tools = anyio.run(listed)
    names = [tool.name for tool in tools]
    if sorted(names) != sorted(TOOL_NAMES) or len(set(names)) != len(names):
        raise SystemExit(f"tools/list {sorted(names)} disagrees with TOOL_NAMES {sorted(TOOL_NAMES)}")
    for tool in tools:
        if (tool.input_schema or {}).get("type") != "object":
            raise SystemExit(f"tool {tool.name} has no object input schema")
    return [{"name": name, "deferred": False} for name in sorted(names)]


def render(entries: list[dict]) -> str:
    return json.dumps(entries, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true", help="fail if --out differs from the live tool surface")
    args = parser.parse_args(argv)

    text = render(inventory())
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != text:
            print(f"{args.out} is stale; run scripts/gen_tool_inventory.py", file=sys.stderr)
            return 1
        print(f"{args.out} matches the MCP tool surface", file=sys.stderr)
        return 0
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
