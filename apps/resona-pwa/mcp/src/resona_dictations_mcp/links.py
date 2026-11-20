"""Signed, expiring links that move audio bytes past the model.

``create_audio_upload`` and ``get_audio_link`` hand out URLs under ``/mcp/links/``. A
link token is ``<kind>.<link id>.<expiry>.<HMAC-SHA256>`` keyed with
``DICTATIONS_MCP_LINK_SECRET``: forged, altered or expired tokens are refused before any
lookup. The token carries no identity and no Directus token. What a link may do (the
user, a Directus client holding their token, the recording) stays in this process,
keyed by the link id, until the link expires; an upload grant is removed by the first
request that presents it, whatever that request's outcome, so a link uploads at most once.

Assumption: one instance of this service. Grants live in memory, so after a restart (or
on a second replica) a link is unknown and refused like a used one: fail-closed.
Directus has no field to record a link's use, and the ``user`` role must not get one.

Uploads stream into Directus files as the user, then create the recording ``pending``
for the project worker. The size cap (``Content-Length``, required) and the magic-byte
check run before the first byte reaches Directus. No hub call.
"""
import base64
import hashlib
import hmac
import logging
import re
import secrets
import time
import unicodedata
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Optional

from starlette.background import BackgroundTask
from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.types import Receive, Scope, Send

from .directus import Directus, DirectusError, DirectusUnavailable, NotFound, Unauthorized

log = logging.getLogger(__name__)

LINKS_PATH = "/mcp/links"
UPLOAD_LINK_SECONDS = 600
AUDIO_LINK_SECONDS = 600
# The Resona hub's default upload cap (RESONA_MAX_UPLOAD_BYTES): the project worker
# hands the file to the hub unchanged, so a larger one could never be transcribed.
DEFAULT_MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MIN_SECRET_CHARS = 32
MAX_GRANTS = 10_000
_SNIFF_BYTES = 12
_KINDS = ("upload", "audio")
_TOKEN_IN_PATH = re.compile(r"(/mcp/links/[a-z]+/)[^\s?\"]+")


class LinkConfigError(Exception):
    """Links are configured, but not usably (short secret, no public URL)."""


class TooManyLinks(Exception):
    """Too many unexpired links are outstanding."""


class _BodyMismatch(Exception):
    """The request body ended before, or ran past, its ``Content-Length``."""


def sniff_audio(head: bytes) -> Optional[tuple[str, str]]:
    """``(MIME type, file extension)`` from the leading magic bytes, or None.

    The audio the Resona hub admits (its upload ``sniff_media``), since the project
    worker uploads the file to the hub unchanged."""
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav", "wav"
    if head.startswith(b"ID3"):
        return "audio/mpeg", "mp3"
    if len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0:
        # ADTS (AAC) shares the MPEG frame sync; its layer bits are 00, reserved in MPEG audio.
        return ("audio/aac", "aac") if head[1] & 0x06 == 0 else ("audio/mpeg", "mp3")
    if head.startswith(b"OggS"):
        return "audio/ogg", "ogg"
    if head.startswith(b"fLaC"):
        return "audio/flac", "flac"
    if head[4:8] == b"ftyp":
        return "audio/mp4", "m4a"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "audio/webm", "webm"
    return None


def directus_filename(name: str, extension: str) -> str:
    """The stored file name: the base name's stem in ASCII-safe characters plus the
    extension that matches the sniffed type (Directus serves the type it is given)."""
    base = re.split(r"[/\\]", name)[-1]
    stem = base.rsplit(".", 1)[0] if "." in base.lstrip(".") else base
    ascii_stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    clean = re.sub(r"[^A-Za-z0-9 ._()+-]+", "_", ascii_stem).strip(" ._")[:100]
    return f"{clean or 'audio'}.{extension}"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class LinkSigner:
    """HMAC-SHA256 over ``<kind>.<link id>.<expiry>``."""

    def __init__(self, secret: str):
        if len(secret) < MIN_SECRET_CHARS:
            raise LinkConfigError(f"DICTATIONS_MCP_LINK_SECRET must have at least {MIN_SECRET_CHARS} characters")
        self._key = hashlib.sha256(b"resona-dictations-mcp/links/v1\0" + secret.encode()).digest()

    def _mac(self, payload: str) -> str:
        return _b64(hmac.new(self._key, payload.encode(), hashlib.sha256).digest())

    def sign(self, kind: str, link_id: str, expires: int) -> str:
        payload = f"{kind}.{link_id}.{expires}"
        return f"{payload}.{self._mac(payload)}"

    def verify(self, kind: str, token: str) -> Optional[tuple[str, int]]:
        """``(link id, expiry)`` of a token this signer issued for ``kind``, else None."""
        parts = token.split(".")
        if len(parts) != 4 or parts[0] != kind or not re.fullmatch(r"[0-9a-f]{32}", parts[1]) \
                or not parts[2].isdigit():
            return None
        if not hmac.compare_digest(self._mac(".".join(parts[:3])), parts[3]):
            return None
        return parts[1], int(parts[2])


@dataclass(frozen=True)
class Grant:
    """What one link may do, held server-side until it expires."""

    kind: str
    user_id: str
    directus: Directus
    recording_id: str
    expires: int
    # upload only: the new recording's fields
    filename: str = ""
    title: str = ""
    language: Optional[str] = None
    profile: Optional[str] = None


def _error(status: int, code: str, message: str, **headers: str) -> JSONResponse:
    return JSONResponse({"error": code, "message": message}, status_code=status, headers=headers or None)


def _content_length(headers: Headers) -> Optional[int]:
    raw = headers.get("content-length")
    if raw is None or not raw.isdigit() or "transfer-encoding" in headers:
        return None
    return int(raw)


class Links:
    """Issues links (for the tools) and serves ``/mcp/links/<kind>/<token>`` (ASGI)."""

    def __init__(
        self,
        *,
        secret: str,
        public_url: str,
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
        clock: Callable[[], float] = time.time,
        max_grants: int = MAX_GRANTS,
    ):
        if not re.match(r"^https?://[^/\s]+", public_url or ""):
            raise LinkConfigError("DICTATIONS_MCP_PUBLIC_URL must be the app's http(s) origin")
        self._signer = LinkSigner(secret)
        self._base = public_url.rstrip("/") + LINKS_PATH
        self.max_upload_bytes = max_upload_bytes
        self._clock = clock
        self._max_grants = max_grants
        self._grants: dict[str, Grant] = {}

    # ── issuing ──────────────────────────────────────────────────────────

    def _issue(self, kind: str, ttl: int, **fields: Any) -> tuple[str, int]:
        now = int(self._clock())
        self._grants = {k: g for k, g in self._grants.items() if g.expires > now}
        if len(self._grants) >= self._max_grants:
            raise TooManyLinks()
        link_id = secrets.token_hex(16)
        expires = now + ttl
        self._grants[link_id] = Grant(kind=kind, expires=expires, **fields)
        return f"{self._base}/{kind}/{self._signer.sign(kind, link_id, expires)}", expires

    def upload_link(
        self, *, user_id: str, directus: Directus, recording_id: str, filename: str, title: str,
        language: Optional[str], profile: Optional[str],
    ) -> tuple[str, int]:
        """A single-use upload URL that creates recording ``recording_id``; and its expiry."""
        return self._issue("upload", UPLOAD_LINK_SECONDS, user_id=user_id, directus=directus,
                           recording_id=recording_id, filename=filename, title=title,
                           language=language, profile=profile)

    def audio_link(self, *, user_id: str, directus: Directus, recording_id: str) -> tuple[str, int]:
        """A URL for the recording's audio, read as ``directus``' user; and its expiry."""
        return self._issue("audio", AUDIO_LINK_SECONDS, user_id=user_id, directus=directus,
                           recording_id=recording_id)

    # ── serving ──────────────────────────────────────────────────────────

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        started = time.monotonic()
        request = Request(scope, receive)
        kind, _, token = scope["path"][len(LINKS_PATH) + 1:].partition("/")
        entry = {"user": "-", "outcome": "ok", "id": "-"}
        if kind == "upload" and request.method in ("PUT", "POST"):
            response = await self._upload(request, token, entry)
        elif kind == "audio" and request.method == "GET":
            response = await self._audio(request, token, entry)
        elif kind in _KINDS:
            response = _error(405, "method_not_allowed", "method not allowed",
                              Allow="PUT, POST" if kind == "upload" else "GET")
        else:
            response = _error(404, "not_found", "not found")
        if response.status_code >= 400 and entry["outcome"] == "ok":
            entry["outcome"] = str(response.status_code)
        log.info("link=%s user=%s outcome=%s ids=%s ms=%d", kind if kind in _KINDS else "-", entry["user"],
                 entry["outcome"], entry["id"], (time.monotonic() - started) * 1000)
        await response(scope, receive, send)

    def _check(self, kind: str, token: str, entry: dict[str, str], *, consume: bool) -> Response | Grant:
        link = self._signer.verify(kind, token)
        if link is None:
            entry["outcome"] = "invalid_link"
            return _error(404, "invalid_link", "unknown link")
        link_id, expires = link
        grant = self._grants.pop(link_id, None) if consume else self._grants.get(link_id)
        if expires <= self._clock():
            self._grants.pop(link_id, None)
            entry["outcome"] = "link_expired"
            return _error(410, "link_expired", "this link has expired; ask for a new one")
        if grant is None or grant.kind != kind:
            entry["outcome"] = "link_used"
            return _error(410, "link_used", "this link was already used or is no longer known; ask for a new one")
        entry.update(user=grant.user_id, id=grant.recording_id)
        return grant

    async def _upload(self, request: Request, token: str, entry: dict[str, str]) -> Response:
        # Spent by the first attempt, before anything else is checked.
        grant = self._check("upload", token, entry, consume=True)
        if isinstance(grant, Response):
            return grant
        length = _content_length(request.headers)
        if length is None:
            return _error(411, "length_required", "send the file with a Content-Length, e.g. curl -T <file> <url>")
        if length > self.max_upload_bytes:
            return _error(413, "too_large", f"the file exceeds {self.max_upload_bytes} bytes")
        if length == 0:
            return _error(422, "empty", "the file is empty")
        stream = request.stream()
        head = b""
        try:
            while len(head) < _SNIFF_BYTES:
                try:
                    head += await anext(stream)
                except StopAsyncIteration:
                    break
        except ClientDisconnect:
            return _error(400, "upload_incomplete", "the upload ended early")
        sniffed = sniff_audio(head)
        if sniffed is None:
            return _error(415, "unsupported_media_type",
                          "not an audio file the app accepts (WAV, MP3, AAC, Ogg, FLAC, MP4/M4A, WebM)")
        mime, extension = sniffed

        async def body() -> AsyncIterator[bytes]:
            received = len(head)
            if received > length:
                raise _BodyMismatch()
            yield head
            async for chunk in stream:
                received += len(chunk)
                if received > length:
                    raise _BodyMismatch()
                if chunk:
                    yield chunk
            if received != length:
                raise _BodyMismatch()

        try:
            file_id = await grant.directus.upload_file(
                body(), length=length, filename=directus_filename(grant.filename, extension), content_type=mime)
            row: dict[str, Any] = {
                "id": grant.recording_id, "title": grant.title, "title_auto": True, "audio_file": file_id,
                "source": "batch", "status": "pending", "duration_seconds": 0,
            }
            if grant.language:
                row["language"] = grant.language
            if grant.profile:
                row["profile"] = grant.profile
            await grant.directus.create("recordings", row, fields=["id"])
        except (_BodyMismatch, ClientDisconnect):
            return _error(400, "upload_incomplete", "the body did not match its Content-Length")
        except Exception as exc:  # noqa: BLE001 - mapped to a status, logged by outcome only
            return self._directus_failure(exc, entry)
        return JSONResponse({"id": grant.recording_id, "status": "pending"}, status_code=201)

    async def _audio(self, request: Request, token: str, entry: dict[str, str]) -> Response:
        grant = self._check("audio", token, entry, consume=False)
        if isinstance(grant, Response):
            return grant
        try:
            row = await grant.directus.item("recordings", grant.recording_id, fields=["audio_file"])
            if not row.get("audio_file"):
                entry["outcome"] = "no_audio"
                return _error(404, "no_audio", "this dictation has no audio")
            upstream = await grant.directus.open_asset(str(row["audio_file"]), byte_range=request.headers.get("range"))
        except Exception as exc:  # noqa: BLE001
            return self._directus_failure(exc, entry)
        headers = {name: upstream.headers[name] for name in (
            "content-type", "content-length", "content-range", "accept-ranges", "last-modified", "etag",
        ) if name in upstream.headers}
        headers["cache-control"] = "private, no-store"
        return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code, headers=headers,
                                 background=BackgroundTask(upstream.aclose))

    @staticmethod
    def _directus_failure(exc: Exception, entry: dict[str, str]) -> Response:
        if isinstance(exc, Unauthorized):
            entry["outcome"] = "unauthorized"
            return _error(401, "token_rejected", "the access token behind this link is no longer valid")
        if isinstance(exc, NotFound):
            entry["outcome"] = "not_found"
            return _error(404, "not_found", "not found for this user")
        if isinstance(exc, DirectusUnavailable):
            entry["outcome"] = "unavailable"
            return _error(503, "directus_unavailable", "the dictation store is unavailable; try again later")
        if isinstance(exc, DirectusError):
            entry["outcome"] = f"directus_{exc.status}"
            if exc.status == 416:
                return _error(416, "range_not_satisfiable", "range not satisfiable")
            return _error(502, "directus_error", f"the dictation store refused the request ({exc.status})")
        raise exc


class RedactLinkTokens(logging.Filter):
    """Drops link tokens from log records (uvicorn's access log has the request path)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _TOKEN_IN_PATH.sub(r"\1…", arg) if isinstance(arg, str) else arg for arg in record.args)
        if isinstance(record.msg, str):
            record.msg = _TOKEN_IN_PATH.sub(r"\1…", record.msg)
        return True
