"""No Directus needed: token verifier, cursor paging, tool surface and compose review."""
import importlib.util
import json
from pathlib import Path

import anyio
import httpx
import pytest
import respx
import yaml
from mcp import Client

from resona_dictations_mcp.directus import DirectusUnavailable, new_http_client
from resona_dictations_mcp.http import DirectusTokenVerifier, create_app
from resona_dictations_mcp.server import TOOL_NAMES, Caller, build_server

from .conftest import INIT, free_port, headers, serve, tool_error, tool_payload

MCP_DIR = Path(__file__).resolve().parents[1]
APP_DIR = MCP_DIR.parent


@respx.mock
def test_verifier_asks_directus_as_the_token_and_caches_only_acceptance():
    route = respx.get("http://directus.test/users/me")
    route.side_effect = [
        httpx.Response(200, json={"data": {"id": "user-a"}}),
        httpx.Response(401, json={"errors": [{"message": "Invalid user credentials."}]}),
        httpx.Response(401, json={"errors": []}),
        httpx.Response(503),
        httpx.Response(200, json={"data": {"id": "user-a"}}),
    ]

    async def go():
        async with new_http_client("http://directus.test") as http:
            verify = DirectusTokenVerifier(http)
            assert await verify("tok-a") == "user-a"
            assert await verify("tok-a") == "user-a"  # cached, no second Directus call
            assert route.call_count == 1
            assert route.calls.last.request.headers["Authorization"] == "Bearer tok-a"
            assert await verify("tok-x") is None
            assert await verify("tok-x") is None  # rejections are re-checked
            assert route.call_count == 3
            with pytest.raises(DirectusUnavailable):
                await verify("tok-y")
            verify.forget("tok-a")
            assert await verify("tok-a") == "user-a"
            assert route.call_count == 5

    anyio.run(go)


def test_unreachable_directus_is_503_not_401():
    app = create_app(directus_url=f"http://127.0.0.1:{free_port()}", allowed_hosts=["127.0.0.1:*"])
    with serve(app) as url:
        resp = httpx.post(f"{url}/mcp", json=INIT, headers=headers("some-token"))
    assert resp.status_code == 503


class FakeDirectus:
    """Recordings only, with Directus' sort/filter/offset semantics for the list query."""

    def __init__(self, rows):
        self.rows = rows

    async def items(self, collection, *, fields, filter=None, sort=None, limit, offset=0):
        if collection != "recordings":
            return []
        rows = sorted(self.rows, key=lambda r: (r["date_created"], r["id"]), reverse=True)
        for condition in (filter or {}).get("_and", []):
            bound = condition["date_created"]["_lte"]
            rows = [r for r in rows if r["date_created"] <= bound]
        return rows[offset:offset + limit]


def test_cursor_pages_through_rows_created_in_the_same_instant():
    stamps = ["2026-03-03T10:00:00.000Z"] + ["2026-03-02T10:00:00.000Z"] * 5 + ["2026-03-01T10:00:00.000Z"]
    rows = [{"id": f"r{i}", "date_created": stamp} for i, stamp in enumerate(stamps)]
    fake = FakeDirectus(rows)
    server = build_server(lambda ctx: Caller(user_id="u", directus=fake))

    async def go():
        seen, cursor = [], None
        async with Client(server) as client:
            for _ in range(10):
                args = {"limit": 2, **({"cursor": cursor} if cursor else {})}
                page = tool_payload(await client.call_tool("list_dictations", args))
                seen += [d["id"] for d in page["dictations"]]
                cursor = page["next_cursor"]
                if cursor is None:
                    break
                # A dictation saved between two pages never shifts the next one.
                fake.rows.append({"id": f"new{len(fake.rows)}", "date_created": "2026-03-04T10:00:00.000Z"})
            limits = tool_error(await client.call_tool("list_dictations", {"limit": 51}))
            chunk = tool_error(await client.call_tool(
                "get_dictation", {"id": "00000000-0000-0000-0000-000000000000", "limit": 32769}))
        return seen, limits, chunk

    seen, limits, chunk = anyio.run(go)
    assert seen == ["r0", "r5", "r4", "r3", "r2", "r1", "r6"]
    assert "limit" in limits and "limit" in chunk


def test_tool_inventory_matches_the_registered_tools():
    spec = importlib.util.spec_from_file_location("gen_tool_inventory", MCP_DIR / "scripts" / "gen_tool_inventory.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    entries = gen.inventory()

    assert (MCP_DIR / "tool-inventory.json").read_text(encoding="utf-8") == gen.render(entries)
    assert [e["name"] for e in entries] == sorted(TOOL_NAMES)
    assert json.loads(gen.render(entries)) == entries


def test_every_tool_says_that_its_output_goes_to_the_clients_model():
    async def go():
        async with Client(build_server(lambda ctx: None)) as client:
            return (await client.list_tools()).tools

    for tool in anyio.run(go):
        assert "sent to the model of the client" in tool.description, tool.name


@pytest.mark.parametrize("compose", ["docker-compose.yml", "docker-compose.backend.yml", "docker-compose.coolify.yml"])
def test_compose_service_gets_no_admin_token_and_no_hub(compose):
    services = yaml.safe_load((APP_DIR / compose).read_text(encoding="utf-8"))["services"]
    service = services["dictations-mcp"]
    env = service["environment"]

    assert set(env) <= {"DIRECTUS_URL", "DICTATIONS_MCP_HOST", "DICTATIONS_MCP_PORT",
                        "DICTATIONS_MCP_ALLOWED_HOSTS", "DICTATIONS_MCP_ALLOWED_ORIGINS", "LOGLEVEL"}
    assert env["DIRECTUS_URL"] == "http://directus:8055"
    for value in env.values():
        for secret in ("DIRECTUS_TOKEN", "ADMIN", "RESONA_API", "SECRET", "PASSWORD"):
            assert secret not in str(value)
    assert "env_file" not in service and "volumes" not in service
    if compose == "docker-compose.backend.yml":
        # No PWA container here: the dev server proxies to a loopback-only port.
        assert all(str(port).startswith("127.0.0.1:") for port in service["ports"])
    else:
        assert "ports" not in service
        assert services["pwa"]["environment"]["NUXT_DICTATIONS_MCP_INTERNAL_URL"] == "http://dictations-mcp:8000"
