"""Streamable HTTP at ``/mcp`` with the calling user's own Directus token.

Every HTTP request must carry ``Authorization: Bearer <Directus token>``. The token is
verified with Directus ``GET /users/me`` (accepted tokens are cached by hash for 30 s)
and then used, unchanged, for that request's Directus calls. The server holds no
Directus credential of its own, so Directus' ``user`` policy decides every access.
Protocol sessions are bound to the token hash that opened them (the SDK answers 404
for a session id presented with another token). Host/Origin headers are checked
against ``DICTATIONS_MCP_ALLOWED_HOSTS`` / ``DICTATIONS_MCP_ALLOWED_ORIGINS`` before
authentication. Nothing here logs a token or a request body.

``/mcp/links/…`` (``links.py``) takes no bearer token: a signed link is the credential
there, and the user's Directus client is held server-side for the link's lifetime.
"""
import contextlib
import hashlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Optional

import httpx
from decouple import Csv, config
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from starlette.applications import Starlette
from starlette.authentication import AuthCredentials
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .directus import Directus, DirectusError, DirectusUnavailable, new_http_client
from .links import DEFAULT_MAX_UPLOAD_BYTES, LINKS_PATH, Links
from .server import Caller, build_server

log = logging.getLogger(__name__)

MCP_PATH = "/mcp"
# JSON-RPC envelopes; the largest is create_dictation_from_text's text (≤ 200000 chars,
# escaped). Audio never comes this way (upload links).
MAX_REQUEST_BODY_BYTES = 2 * 1024 * 1024
DEFAULT_ALLOWED_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*", "127.0.0.1", "localhost"]
DEFAULT_ALLOWED_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class DirectusTokenVerifier:
    """Maps a bearer token to its Directus user id, or None when Directus rejects it.

    Accepted tokens are cached by fingerprint for ``ttl`` seconds; rejections are not
    cached. Tool calls reach Directus with the same token anyway, so a revoked token
    loses data access immediately; the first such rejection also drops the cache entry
    (``forget``), so the token is 401 from the next request on, at the latest after ``ttl``.
    """

    def __init__(self, http: httpx.AsyncClient, *, ttl: float = 30.0, max_entries: int = 1024):
        self._http = http
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
            user_id = await Directus(self._http, token).me()
        except DirectusUnavailable:
            raise
        except DirectusError as exc:
            self._cache.pop(key, None)
            if exc.status in (401, 403, 404):
                return None
            raise DirectusUnavailable() from None
        if len(self._cache) >= self._max_entries:
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
            if len(self._cache) >= self._max_entries:
                self._cache.clear()
        self._cache[key] = (now + self._ttl, user_id)
        return user_id

    def forget(self, token: str) -> None:
        self._cache.pop(token_fingerprint(token), None)


TokenVerifier = Callable[[str], Awaitable[Optional[str]]]


def _bearer(headers: Headers) -> Optional[str]:
    scheme, _, token = headers.get("authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return None
    return token


class BearerAuth:
    """ASGI middleware: Host/Origin check, then a Directus-verified bearer token on every request."""

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
            user_id = await self.verify(token)
        except DirectusUnavailable:
            await JSONResponse({"error": "directus_unavailable"}, status_code=503)(scope, receive, send)
            return
        if user_id is None:
            await _unauthorized("invalid or revoked token")(scope, receive, send)
            return
        # The SDK binds each protocol session to this principal and refuses the
        # session id when a request presents a different credential.
        scope["user"] = AuthenticatedUser(AccessToken(
            token=token,
            client_id=token_fingerprint(token),
            scopes=[],
            subject=user_id,
        ))
        scope["auth"] = AuthCredentials([])
        await self.app(scope, receive, send)


def _unauthorized(description: str) -> JSONResponse:
    return JSONResponse(
        {"error": "invalid_token", "error_description": description},
        status_code=401,
        headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
    )


def request_principal(ctx: Context) -> AccessToken:
    """Token and user id that ``BearerAuth`` verified for the HTTP request carrying this tool call."""
    scope = getattr(ctx.request_context.request, "scope", None) or {}
    user = scope.get("user")
    if not isinstance(user, AuthenticatedUser) or not user.access_token.subject:
        raise ToolError("unauthenticated request")
    return user.access_token


def create_app(
    *,
    directus_url: Optional[str] = None,
    allowed_hosts: Optional[list[str]] = None,
    allowed_origins: Optional[list[str]] = None,
    token_ttl: float = 30.0,
    link_secret: Optional[str] = None,
    public_url: Optional[str] = None,
    max_upload_bytes: Optional[int] = None,
) -> ASGIApp:
    """The ASGI app serving Streamable HTTP at ``/mcp`` and the links under
    ``/mcp/links/``; serve it as the top-level app. Unset arguments come from the
    environment (``DICTATIONS_MCP_LINK_SECRET``, ``DICTATIONS_MCP_PUBLIC_URL``, …)."""
    links = _links(link_secret, public_url, max_upload_bytes)
    url = directus_url or config("DIRECTUS_URL", default="http://127.0.0.1:7700")
    hosts = allowed_hosts if allowed_hosts is not None else (
        config("DICTATIONS_MCP_ALLOWED_HOSTS", default="", cast=Csv()) or DEFAULT_ALLOWED_HOSTS
    )
    origins = allowed_origins if allowed_origins is not None else (
        config("DICTATIONS_MCP_ALLOWED_ORIGINS", default="", cast=Csv()) or DEFAULT_ALLOWED_ORIGINS
    )
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(hosts),
        allowed_origins=list(origins),
    )
    http = new_http_client(url)
    verifier = DirectusTokenVerifier(http, ttl=token_ttl)

    def caller_for(ctx: Context) -> Caller:
        principal = request_principal(ctx)
        token = principal.token
        return Caller(
            user_id=str(principal.subject),
            directus=Directus(http, token),
            token_rejected=lambda: verifier.forget(token),
        )

    app: Starlette = build_server(caller_for, links).streamable_http_app(
        streamable_http_path=MCP_PATH,
        transport_security=security,
        max_request_body_size=MAX_REQUEST_BODY_BYTES,
    )
    inner_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(starlette_app: Starlette) -> AsyncIterator[None]:
        async with inner_lifespan(starlette_app):
            try:
                yield
            finally:
                await http.aclose()

    app.router.lifespan_context = lifespan
    return LinkRoutes(BearerAuth(app, verifier, security), links, security)


def _links(secret: Optional[str], public_url: Optional[str], max_upload_bytes: Optional[int]) -> Optional[Links]:
    """The link issuer, or None without a link secret (link routes and tools then refuse)."""
    secret = secret if secret is not None else config("DICTATIONS_MCP_LINK_SECRET", default="")
    if not secret:
        log.info("DICTATIONS_MCP_LINK_SECRET is not set: upload and audio links are disabled")
        return None
    return Links(
        secret=secret,
        public_url=public_url if public_url is not None else config("DICTATIONS_MCP_PUBLIC_URL", default=""),
        max_upload_bytes=max_upload_bytes if max_upload_bytes is not None else config(
            "DICTATIONS_MCP_MAX_UPLOAD_BYTES", default=DEFAULT_MAX_UPLOAD_BYTES, cast=int),
    )


class LinkRoutes:
    """ASGI: ``/mcp/links/…`` goes to the link handler, everything else to ``app``.

    A link is its own credential (no bearer token); Host/Origin are checked like for
    ``/mcp``. Without links (no secret) the paths are 404."""

    def __init__(self, app: ASGIApp, links: Optional[Links], security: TransportSecuritySettings):
        self.app = app
        self.links = links
        self.security = TransportSecurityMiddleware(security)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(LINKS_PATH + "/"):
            await self.app(scope, receive, send)
            return
        rejected = await self.security.validate_request(Request(scope), is_post=False)
        if rejected is not None:
            await rejected(scope, receive, send)
            return
        if self.links is None:
            await JSONResponse({"error": "links_disabled", "message": "links are not enabled on this server"},
                               status_code=404)(scope, receive, send)
            return
        await self.links(scope, receive, send)
