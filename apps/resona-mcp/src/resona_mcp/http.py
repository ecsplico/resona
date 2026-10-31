"""Hosted MCP: Streamable HTTP at ``/mcp`` with per-request organization bearer tokens.

Every HTTP request must carry ``Authorization: Bearer <org token>``. The token is
verified against the hub and then used, unchanged, for that request's hub calls; the
server holds no key of its own, so organization isolation is enforced by the hub.
Protocol sessions are bound to the credential that opened them (the SDK refuses a
session id presented with a different token). Host/Origin headers are checked against
``RESONA_MCP_ALLOWED_HOSTS`` / ``RESONA_MCP_ALLOWED_ORIGINS`` before authentication.
"""
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Optional

import httpx
from decouple import Csv, config
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from resona_client.client import ResonaClient
from starlette.applications import Starlette
from starlette.authentication import AuthCredentials
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .server import InputPolicy, build_server

log = logging.getLogger(__name__)

MCP_PATH = "/mcp"
# 8 MiB decoded upload_content is ~10.7 MiB of base64 plus the JSON-RPC envelope.
MAX_REQUEST_BODY_BYTES = 12 * 1024 * 1024
DEFAULT_ALLOWED_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*", "127.0.0.1", "localhost"]
DEFAULT_ALLOWED_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]

TokenVerifier = Callable[[str], Awaitable[Optional[str]]]
"""Maps a bearer token to its organization id, or None when the hub rejects it."""


class HubUnavailable(Exception):
    """The hub could not confirm or reject a token."""


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class HubTokenVerifier:
    """Verifies a token by reading the organization's settings with it (client/admin roles).

    Accepted tokens are cached by fingerprint for ``ttl`` seconds; rejections are not
    cached. Tool calls hit the hub with the same token anyway, so a revoked credential
    loses data access immediately and MCP access within ``ttl``.
    """

    def __init__(self, hub_url: str, *, ttl: float = 30.0, max_entries: int = 1024):
        self._hub_url = hub_url.rstrip("/")
        self._ttl = ttl
        self._max_entries = max_entries
        self._cache: dict[str, tuple[float, str]] = {}

    async def __call__(self, token: str) -> Optional[str]:
        key = token_fingerprint(token)
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self._hub_url}/v1/settings",
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.TransportError:
            raise HubUnavailable() from None
        if resp.status_code in (401, 403):
            self._cache.pop(key, None)
            return None
        if resp.status_code != 200:
            raise HubUnavailable()
        organization_id = str(resp.json()["organization_id"])
        if len(self._cache) >= self._max_entries:
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
            if len(self._cache) >= self._max_entries:
                self._cache.clear()
        self._cache[key] = (now + self._ttl, organization_id)
        return organization_id


def _bearer(headers: Headers) -> Optional[str]:
    scheme, _, token = headers.get("authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return None
    return token


class BearerAuth:
    """ASGI middleware: Host/Origin check, then a hub-verified bearer token on every request."""

    def __init__(self, app: ASGIApp, verify: TokenVerifier, security: TransportSecuritySettings):
        self.app = app
        self.verify = verify
        self.security = TransportSecurityMiddleware(security)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rejected = await self.security.validate_request(Request(scope), is_post=False)
        if rejected is not None:
            await rejected(scope, receive, send)
            return
        token = _bearer(Headers(scope=scope))
        if token is None:
            await _unauthorized("missing bearer token")(scope, receive, send)
            return
        try:
            organization_id = await self.verify(token)
        except HubUnavailable:
            await JSONResponse({"error": "hub_unavailable"}, status_code=503)(scope, receive, send)
            return
        if organization_id is None:
            await _unauthorized("invalid or revoked token")(scope, receive, send)
            return
        # The SDK binds each protocol session to this principal and refuses the
        # session id when a request presents a different credential.
        scope["user"] = AuthenticatedUser(AccessToken(
            token=token,
            client_id=token_fingerprint(token),
            scopes=[],
            subject=organization_id,
        ))
        scope["auth"] = AuthCredentials([])
        await self.app(scope, receive, send)


def _unauthorized(description: str) -> JSONResponse:
    return JSONResponse(
        {"error": "invalid_token", "error_description": description},
        status_code=401,
        headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
    )


def request_token(ctx: Context) -> str:
    """The bearer token of the HTTP request carrying this tool call."""
    headers = ctx.headers
    token = _bearer(Headers(headers=dict(headers))) if headers is not None else None
    if token is None:
        raise ToolError("unauthenticated request")
    return token


def create_app(
    *,
    hub_url: Optional[str] = None,
    allowed_hosts: Optional[list[str]] = None,
    allowed_origins: Optional[list[str]] = None,
    verify: Optional[TokenVerifier] = None,
    client_for_token: Optional[Callable[[str], ResonaClient]] = None,
) -> ASGIApp:
    """The hosted MCP ASGI app serving Streamable HTTP at ``/mcp``.

    Its lifespan runs the MCP session manager: serve it as the top-level app, or run
    its lifespan when mounting it inside another ASGI application.
    """
    hub = (hub_url or config("RESONA_API_URL", default="http://127.0.0.1:7000")).rstrip("/")
    hosts = allowed_hosts if allowed_hosts is not None else (
        config("RESONA_MCP_ALLOWED_HOSTS", default="", cast=Csv()) or DEFAULT_ALLOWED_HOSTS
    )
    origins = allowed_origins if allowed_origins is not None else (
        config("RESONA_MCP_ALLOWED_ORIGINS", default="", cast=Csv()) or DEFAULT_ALLOWED_ORIGINS
    )
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(hosts),
        allowed_origins=list(origins),
    )
    make_client = client_for_token or (lambda token: ResonaClient(base_url=hub, api_key=token, timeout=120.0))

    server = build_server(
        lambda ctx: make_client(request_token(ctx)),
        InputPolicy(allow_paths=False),
    )
    app: Starlette = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        transport_security=security,
        max_request_body_size=MAX_REQUEST_BODY_BYTES,
    )
    return BearerAuth(app, verify or HubTokenVerifier(hub), security)
