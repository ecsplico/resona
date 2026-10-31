"""Hosted Streamable HTTP: bearer auth, credential-bound sessions, Host/Origin checks."""
import socket
import threading
import time
from contextlib import contextmanager

import anyio
import httpx
import pytest
import respx
import uvicorn
from mcp import Client, ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from resona_mcp.http import HubTokenVerifier, HubUnavailable, create_app

TOKENS = {"tok-a": "org-a", "tok-b": "org-b"}
ACCEPT = "application/json, text/event-stream"
INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "test", "version": "1"}},
}


async def fake_verify(token: str):
    return TOKENS.get(token)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def serve(app):
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def base(hub):
    app = create_app(
        hub_url="http://hub.invalid",
        allowed_hosts=["127.0.0.1:*"],
        allowed_origins=["https://app.example"],
        verify=fake_verify,
        client_for_token=hub.client,
    )
    with serve(app) as url:
        yield url


def _headers(token: str | None = None, **extra: str) -> dict[str, str]:
    headers = {"Accept": ACCEPT, "Content-Type": "application/json", **extra}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def test_request_without_bearer_is_401(base):
    resp = httpx.post(f"{base}/mcp", json=INIT, headers=_headers())
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"].startswith("Bearer")


def test_request_with_unknown_token_is_401(base):
    resp = httpx.post(f"{base}/mcp", json=INIT, headers=_headers("forged"))
    assert resp.status_code == 401


def test_bad_origin_is_rejected_even_with_valid_token(base):
    resp = httpx.post(f"{base}/mcp", json=INIT, headers=_headers("tok-a", Origin="https://evil.example"))
    assert resp.status_code == 403


def test_allowed_origin_is_accepted(base):
    resp = httpx.post(f"{base}/mcp", json=INIT, headers=_headers("tok-a", Origin="https://app.example"))
    assert resp.status_code == 200


def test_bad_host_is_rejected(base):
    resp = httpx.post(f"{base}/mcp", json=INIT, headers=_headers("tok-a", Host="evil.example"))
    assert resp.status_code == 421


def test_session_cannot_be_reused_with_another_token(base, hub):
    opened = httpx.post(f"{base}/mcp", json=INIT, headers=_headers("tok-a"))
    assert opened.status_code == 200
    session = opened.headers["mcp-session-id"]
    common = {"mcp-session-id": session, "mcp-protocol-version": "2025-06-18"}
    httpx.post(f"{base}/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
               headers=_headers("tok-a", **common))
    call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "get_job", "arguments": {"job_id": 4}}}

    hijack = httpx.post(f"{base}/mcp", json=call, headers=_headers("tok-b", **common))
    assert hijack.status_code == 404
    assert hub.tokens == []

    own = httpx.post(f"{base}/mcp", json=call, headers=_headers("tok-a", **common))
    assert own.status_code == 200
    assert hub.tokens == ["tok-a"]


def test_sdk_clients_call_the_hub_with_their_own_token(base, hub, payload):
    async def as_session(token: str):
        http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
        async with http, streamable_http_client(f"{base}/mcp", http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool("get_job", {"job_id": 4})

    async def as_client(token: str):
        http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
        async with http, Client(streamable_http_client(f"{base}/mcp", http_client=http)) as client:
            return await client.call_tool("get_settings", {})

    assert payload(anyio.run(as_session, "tok-a"))["job_id"] == 4
    assert payload(anyio.run(as_client, "tok-b"))["organization_id"] == "org-a"  # fake hub's answer
    assert hub.tokens == ["tok-a", "tok-b"]


def test_remote_path_input_is_rejected_over_http(base, hub, error_text):
    async def go():
        http = create_mcp_http_client(headers={"Authorization": "Bearer tok-a"})
        async with http, streamable_http_client(f"{base}/mcp", http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool("transcribe_audio", {"path": "/etc/hosts"})

    assert "upload_content" in error_text(anyio.run(go))
    assert hub.calls == []


@respx.mock
def test_hub_token_verifier_maps_hub_answers_and_caches_only_acceptance():
    route = respx.get("http://hub.test/v1/settings")
    route.side_effect = [
        httpx.Response(200, json={"organization_id": "org-a", "revision": 1, "settings": {}}),
        httpx.Response(401, json={"error": "unauthorized"}),
        httpx.Response(401, json={"error": "unauthorized"}),
        httpx.Response(503),
    ]
    verify = HubTokenVerifier("http://hub.test")

    assert anyio.run(verify, "tok-a") == "org-a"
    assert anyio.run(verify, "tok-a") == "org-a"  # cached, no second hub call
    assert route.call_count == 1
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok-a"
    assert anyio.run(verify, "tok-x") is None
    assert anyio.run(verify, "tok-x") is None  # rejections are re-checked
    assert route.call_count == 3
    with pytest.raises(HubUnavailable):
        anyio.run(verify, "tok-y")
