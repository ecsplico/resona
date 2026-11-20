"""Fixtures: a real Directus from the app's compose stack, two users, the served MCP app.

The Directus admin login below exists only in the tests, to create users and revoke
tokens. The service under test never receives it: ``create_app`` gets the URL alone.
"""
import json
import os
import secrets
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
import uvicorn

from resona_dictations_mcp.http import create_app

MCP_DIR = Path(__file__).resolve().parents[1]
APP_DIR = MCP_DIR.parent
ACCEPT = "application/json, text/event-stream"
INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "test", "version": "1"}},
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def serve(app, port: int | None = None):
    port = port or free_port()
    # log_config=None: uvicorn's lines go through the root logger, so caplog sees them.
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info", log_config=None))
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


def headers(token: str | None = None, **extra: str) -> dict[str, str]:
    result = {"Accept": ACCEPT, "Content-Type": "application/json", **extra}
    if token:
        result["Authorization"] = f"Bearer {token}"
    return result


@dataclass
class DirectusAdmin:
    """Test-side admin access to the throwaway Directus."""

    url: str
    token: str
    role_id: str
    stamp: str = field(default_factory=lambda: secrets.token_hex(4))

    def call(self, method: str, path: str, token: str | None = None, **kwargs):
        resp = httpx.request(method, f"{self.url}{path}", timeout=30.0,
                             headers={"Authorization": f"Bearer {token or self.token}"}, **kwargs)
        assert resp.status_code < 300, f"{method} {path} -> {resp.status_code}"
        return resp.json()["data"] if resp.content else None

    def user(self, name: str) -> "User":
        token = secrets.token_hex(24)
        row = self.call("POST", "/users", json={
            "email": f"dmc-{name}-{self.stamp}-{secrets.token_hex(3)}@example.com",
            "password": secrets.token_hex(12), "role": self.role_id, "token": token,
        })
        return User(self, row["id"], token)

    def revoke(self, user: "User") -> None:
        self.call("PATCH", f"/users/{user.id}", json={"token": None})


@dataclass
class User:
    admin: DirectusAdmin
    id: str
    token: str

    def dictation(self, title: str, text: str | None, **fields) -> str:
        """A recording (and its transcript) created with the user's own token."""
        review_status = fields.pop("review_status", None)  # not a create field of the user policy
        row = self.admin.call("POST", "/items/recordings", self.token, json={
            "title": title, "source": "live", "status": "done", "language": "de",
            "duration_seconds": 12.5, **fields})
        if review_status:
            self.admin.call("PATCH", f"/items/recordings/{row['id']}", self.token,
                            json={"review_status": review_status})
        if text is not None:
            self.admin.call("POST", "/items/transcripts", self.token,
                            json={"recording": row["id"], "text": text})
        return row["id"]


def _compose(project: str, env: dict[str, str], *args: str) -> None:
    subprocess.run(
        ["docker", "compose", "-p", project,
         "-f", str(APP_DIR / "docker-compose.backend.yml"), "-f", str(MCP_DIR / "tests" / "compose.test.yml"),
         *args],
        cwd=APP_DIR, env={**os.environ, **env}, check=True, timeout=900,
    )


@pytest.fixture(scope="session")
def directus_admin():
    """Directus + bootstrap from the app's compose file, on a free port, removed afterwards.

    ``DMC_TEST_DIRECTUS_URL`` (+ ``ADMIN_EMAIL`` / ``ADMIN_PASSWORD``) points the tests
    at an already running, bootstrapped Directus instead.
    """
    external = os.environ.get("DMC_TEST_DIRECTUS_URL")
    if external:
        url = external.rstrip("/")
        email, password = os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"]
        project = None
    else:
        port = free_port()
        url = f"http://127.0.0.1:{port}"
        email, password = "admin@example.com", secrets.token_hex(12)
        project = f"resona-dmc-test-{port}"
        env = {
            "DMC_TEST_DIRECTUS_PORT": str(port),
            "DIRECTUS_SECRET": secrets.token_hex(16),
            "DIRECTUS_ADMIN_EMAIL": email,
            "DIRECTUS_ADMIN_PASSWORD": password,
            "DIRECTUS_TOKEN": secrets.token_hex(24),
        }
    try:
        if project:
            _compose(project, env, "up", "-d", "--build", "--wait", "directus")
            _compose(project, env, "run", "--rm", "--no-deps", "directus-bootstrap")
        login = httpx.post(f"{url}/auth/login", json={"email": email, "password": password}, timeout=30.0)
        assert login.status_code == 200, "Directus admin login failed"
        token = login.json()["data"]["access_token"]
        roles = httpx.get(f"{url}/roles", params={"filter": json.dumps({"name": {"_eq": "user"}})},
                          headers={"Authorization": f"Bearer {token}"}, timeout=30.0).json()["data"]
        assert roles, "no `user` role: Directus is not bootstrapped"
        yield DirectusAdmin(url, token, roles[0]["id"])
    finally:
        if project:
            _compose(project, env, "down", "-v", "--remove-orphans")


@dataclass
class Stack:
    admin: DirectusAdmin
    a: User
    b: User
    a_ids: list[str]
    b_ids: list[str]


# Markers that must never show up in a log line.
A_TEXT = "Patientin Alpha-Geheimwort berichtet über Kopfschmerzen. " * 3
B_TEXT = "Patient Beta-Geheimwort klagt über Schwindel."


@pytest.fixture(scope="session")
def stack(directus_admin) -> Stack:
    a, b = directus_admin.user("a"), directus_admin.user("b")
    a_ids = [
        a.dictation("Visite Alpha eins", A_TEXT),
        a.dictation("Befund zwei", "Kurzer Befund ohne Besonderheiten.", review_status="ready"),
        a.dictation("Wartet noch", None, status="pending"),
    ]
    b_ids = [b.dictation("Visite Beta", B_TEXT)]
    return Stack(directus_admin, a, b, a_ids, b_ids)


@pytest.fixture(scope="session")
def base(directus_admin):
    """The service as deployed: it knows the Directus URL and nothing else."""
    app = create_app(directus_url=directus_admin.url, allowed_hosts=["127.0.0.1:*"],
                     allowed_origins=["https://app.example"])
    with serve(app) as url:
        yield url


def tool_payload(result) -> dict:
    assert not result.is_error, result.content
    if result.structured_content is not None:
        data = result.structured_content
        return data["result"] if set(data) == {"result"} else data
    return json.loads(result.content[0].text)


def tool_error(result) -> str:
    assert result.is_error, result.content
    return result.content[0].text
