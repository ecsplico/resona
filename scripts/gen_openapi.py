"""Write (or check) ``contract/openapi.json``: the published client contract of the hub.

The document is ``ResonaAPI.openapi_client()`` (only the ``client`` routes and the schemas
they use) with ``info.version`` set to the hub release id, serialized with sorted keys and
two-space indent, so a diff shows exactly what changed. CI compares it with the copy at the
latest ``v*`` tag (``oasdiff breaking``, job ``contract`` in ``test.yml``);
``packages/api/tests/test_contract.py`` fails when it is stale.

    uv run python scripts/gen_openapi.py            # rewrite contract/openapi.json (just contract)
    uv run python scripts/gen_openapi.py --check    # exit 1 if it is stale
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "contract" / "openapi.json"
COMMAND = "uv run python scripts/gen_openapi.py"


def document() -> dict:
    from resona_api.app import app
    from resona_api.releases import hub_release_id

    schema = copy.deepcopy(app.openapi_client())
    schema["info"]["version"] = hub_release_id()
    return schema


def render(schema: dict) -> str:
    return json.dumps(schema, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true", help="fail if --out differs from the live client contract")
    args = parser.parse_args(argv)

    text = render(document())
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != text:
            print(f"{args.out} is stale; run {COMMAND}", file=sys.stderr)
            return 1
        print(f"{args.out} matches the client contract", file=sys.stderr)
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
