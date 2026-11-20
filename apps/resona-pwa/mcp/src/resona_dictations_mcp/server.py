"""The dictation tool surface: the calling user's own dictations (reads here, writes in ``edit_tools``).

Every tool runs as the caller (``Caller.directus`` carries their Directus token); the
server has no ownership logic. A dictation is a ``recordings`` row plus its
``transcripts`` row; the text returned is ``transcripts.text``, the text the app shows.
Log lines carry the tool name, the user id, row ids and the duration, never text.
"""
import base64
import binascii
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal, Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .directus import Directus, DirectusError, DirectusUnavailable, NotFound, Unauthorized

log = logging.getLogger(__name__)

MAX_LIST_LIMIT = 50
MAX_TEXT_CHUNK_CHARS = 32_768

TOOL_NAMES = (
    "list_dictations",
    "search_dictations",
    "get_dictation",
    "update_dictation_text",
    "rename_dictation",
    "set_review_status",
    "delete_dictation",
    "list_documents",
    "get_document",
    "rename_document",
    "delete_document",
)

INSTRUCTIONS = (
    "Read access to the signed-in user's own dictations in the Resona app (recordings "
    "with their transcripts). list_dictations pages through them newest first, "
    "search_dictations finds them by title or transcript text, get_dictation returns "
    "one dictation's metadata and its text in ranges (follow next_offset until it is "
    "null). Everything these tools return, including dictated text, is sent to the "
    "model of the client that calls them. Dictations kept only in a browser "
    "(standalone mode) are not reachable. update_dictation_text, rename_dictation, "
    "set_review_status and delete_dictation change a dictation the way the app does; "
    "deleting cannot be undone. list_documents, get_document (Markdown in "
    "ranges), rename_document and delete_document (confirm=true) work on the user's "
    "converted documents the same way."
)

_SUMMARY_FIELDS = ["id", "title", "date_created", "duration_seconds", "language", "status", "review_status"]
_UUID = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


@dataclass(frozen=True)
class Caller:
    """The user behind one tool call."""

    user_id: str
    directus: Directus
    # Called when Directus rejects the token mid-call, so the next request is 401 at once.
    token_rejected: Callable[[], None] = lambda: None


CallerFactory = Callable[[Context], Caller]

Limit = Annotated[int, Field(default=20, ge=1, le=MAX_LIST_LIMIT, description="How many dictations to return (1-50).")]
Status = Annotated[
    Optional[Literal["pending", "transcribing", "done", "error"]],
    Field(default=None, description="Only dictations in this transcription state."),
]
ReviewStatus = Annotated[
    Optional[Literal["editing", "ready"]],
    Field(default=None, description="Only dictations with this review state ('ready' = released by the user)."),
]
Since = Annotated[
    Optional[str],
    Field(default=None, max_length=40,
          description="Only dictations created at or after this ISO 8601 date or date-time, e.g. '2026-03-01'."),
]
Cursor = Annotated[
    Optional[str],
    Field(default=None, max_length=200, description="next_cursor of the previous page; omit for the first page."),
]
DictationId = Annotated[str, Field(pattern=_UUID, description="Dictation id from list_dictations or search_dictations.")]


def _encode_cursor(created: str, skip: int) -> str:
    raw = json.dumps([created, skip], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[str, int]:
    """A cursor is the last row's creation time plus how many rows of exactly that time
    were already returned (Directus has no ordering operators on uuid ids, so ties are
    skipped by count). Rows created later never shift a page."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        created, skip = json.loads(raw)
        if not isinstance(created, str) or type(skip) is not int or not 0 < skip <= 10_000:
            raise ValueError
        _iso(created)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise ToolError("cursor must be the next_cursor of an earlier list_dictations page") from None
    return created, skip


def text_sha256(text: str) -> str:
    """SHA-256 hex of the UTF-8 text; a dictation without transcript has the empty text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _summary(row: dict[str, Any], text_length: Optional[int]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row.get("title"),
        "created": row.get("date_created"),
        "duration_seconds": row.get("duration_seconds"),
        "language": row.get("language"),
        "status": row.get("status"),
        "review_status": row.get("review_status"),
        "text_length": text_length,
    }


async def _text_lengths(directus: Directus, ids: list[str]) -> dict[str, int]:
    """Characters of each recording's transcript text (absent: no transcript yet)."""
    if not ids:
        return {}
    rows = await directus.items(
        "transcripts", fields=["recording", "text"], filter={"recording": {"_in": ids}}, limit=-1)
    lengths: dict[str, int] = {}
    for row in rows:
        lengths.setdefault(str(row["recording"]), len(row.get("text") or ""))
    return lengths


async def _summaries(directus: Directus, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lengths = await _text_lengths(directus, [row["id"] for row in rows])
    return [_summary(row, lengths.get(row["id"])) for row in rows]


async def _as_caller(
    tool: str, caller: Caller, call: Callable[[], Awaitable[tuple[dict[str, Any], list[str]]]],
    *, noun: str = "dictation",
) -> dict[str, Any]:
    """Run one tool body: map Directus failures to tool errors and write the log line."""
    started = time.monotonic()
    outcome, ids = "ok", []
    try:
        result, ids = await call()
        return result
    except NotFound:
        outcome = "not_found"
        raise ToolError(f"not found: no such {noun} for this user") from None
    except Unauthorized:
        outcome = "unauthorized"
        caller.token_rejected()
        raise ToolError("the access token is no longer valid") from None
    except DirectusUnavailable:
        outcome = "unavailable"
        raise ToolError("the dictation store is unavailable; try again later") from None
    except DirectusError as exc:
        outcome = f"directus_{exc.status}"
        raise ToolError(f"the dictation store refused the request ({exc.status})") from None
    except ToolError:
        outcome = "invalid"
        raise
    finally:
        log.info("tool=%s user=%s outcome=%s ids=%s ms=%d", tool, caller.user_id, outcome,
                 ",".join(ids) or "-", (time.monotonic() - started) * 1000)


def build_server(caller_for: CallerFactory) -> MCPServer:
    """Create the MCP server; ``caller_for(ctx)`` identifies the user behind a tool call."""
    server = MCPServer("resona-dictations", instructions=INSTRUCTIONS)

    @server.tool()
    async def list_dictations(
        ctx: Context,
        limit: Limit = 20,
        cursor: Cursor = None,
        status: Status = None,
        review_status: ReviewStatus = None,
        since: Since = None,
    ) -> dict[str, Any]:
        """List the user's dictations, newest first: id, title, created, duration,
        language, status, review status and transcript length in characters (no text).
        Pass next_cursor as cursor for the following page; null means no more.
        Titles are sent to the model of the client that calls this tool."""
        caller = caller_for(ctx)

        async def call() -> tuple[dict[str, Any], list[str]]:
            conditions: list[dict[str, Any]] = []
            if status is not None:
                conditions.append({"status": {"_eq": status}})
            if review_status is not None:
                conditions.append({"review_status": {"_eq": review_status}})
            if since is not None:
                try:
                    _iso(since)
                except ValueError:
                    raise ToolError("since must be an ISO 8601 date or date-time") from None
                conditions.append({"date_created": {"_gte": since}})
            after, skip = None, 0
            if cursor is not None:
                after, skip = _decode_cursor(cursor)
                conditions.append({"date_created": {"_lte": after}})
            rows = await caller.directus.items(
                "recordings", fields=_SUMMARY_FIELDS, sort=["-date_created", "-id"], limit=limit + 1,
                offset=skip, filter={"_and": conditions} if conditions else None,
            )
            page = rows[:limit]
            next_cursor = None
            if len(rows) > limit:
                last = page[-1]["date_created"]
                same = sum(1 for row in page if row["date_created"] == last)
                # A page made only of rows from the cursor's instant continues its count.
                next_cursor = _encode_cursor(last, same + (skip if same == len(page) and last == after else 0))
            result = {"dictations": await _summaries(caller.directus, page), "next_cursor": next_cursor}
            return result, [row["id"] for row in page]

        return await _as_caller("list_dictations", caller, call)

    @server.tool()
    async def search_dictations(
        ctx: Context,
        query: Annotated[str, Field(min_length=1, max_length=200,
                                    description="Text to look for (case-insensitive substring) in titles and transcripts.")],
        limit: Limit = 20,
    ) -> dict[str, Any]:
        """Find the user's dictations whose title or transcript text contains the query,
        newest first. Returns the same fields as list_dictations plus matched_in
        ('title', 'text' or both), no transcript text; read it with get_dictation.
        Titles are sent to the model of the client that calls this tool."""
        caller = caller_for(ctx)

        async def call() -> tuple[dict[str, Any], list[str]]:
            needle = {"_icontains": query}
            by_title = await caller.directus.items(
                "recordings", fields=["id"], filter={"title": needle},
                sort=["-date_created", "-id"], limit=limit)
            by_text = await caller.directus.items(
                "transcripts", fields=["recording"], filter={"text": needle},
                sort=["-date_created"], limit=limit)
            matched: dict[str, list[str]] = {}
            for row in by_title:
                matched.setdefault(str(row["id"]), []).append("title")
            for row in by_text:
                matched.setdefault(str(row["recording"]), []).append("text")
            if not matched:
                return {"dictations": []}, []
            rows = await caller.directus.items(
                "recordings", fields=_SUMMARY_FIELDS, filter={"id": {"_in": sorted(matched)}},
                sort=["-date_created", "-id"], limit=limit)
            found = await _summaries(caller.directus, rows)
            for entry in found:
                entry["matched_in"] = matched.get(entry["id"], [])
            return {"dictations": found}, [row["id"] for row in rows]

        return await _as_caller("search_dictations", caller, call)

    @server.tool()
    async def get_dictation(
        ctx: Context,
        id: DictationId,
        offset: Annotated[int, Field(default=0, ge=0, description="First character of the text range.")] = 0,
        limit: Annotated[int, Field(default=MAX_TEXT_CHUNK_CHARS, ge=1, le=MAX_TEXT_CHUNK_CHARS,
                                    description="Characters to return (at most 32768).")] = MAX_TEXT_CHUNK_CHARS,
    ) -> dict[str, Any]:
        """Read one dictation: its metadata and one range of its text, the text the app
        shows (a postprocess profile's output when one was applied, see applied_profile).
        Follow next_offset until it is null; total_chars is the whole length and
        text_sha256 identifies the whole text (expected_sha256 of update_dictation_text).
        The text is sent to the model of the client that calls this tool."""
        caller = caller_for(ctx)

        async def call() -> tuple[dict[str, Any], list[str]]:
            row = await caller.directus.item("recordings", id, fields=[*_SUMMARY_FIELDS, "error_message"])
            transcripts = await caller.directus.items(
                "transcripts", fields=["text", "applied_profile", "engine"],
                filter={"recording": {"_eq": id}}, limit=1)
            transcript = transcripts[0] if transcripts else None
            text = (transcript or {}).get("text") or ""
            end = min(offset + limit, len(text))
            result = {
                **_summary(row, len(text) if transcript else None),
                "error_message": row.get("error_message"),
                "applied_profile": (transcript or {}).get("applied_profile"),
                "engine": (transcript or {}).get("engine"),
                "text": text[offset:end],
                "offset": offset,
                "next_offset": end if end < len(text) else None,
                "total_chars": len(text),
                "text_sha256": text_sha256(text),
            }
            return result, [id]

        return await _as_caller("get_dictation", caller, call)

    from . import edit_tools
    edit_tools.register(server, caller_for)
    from . import document_tools  # imports this module's helpers, so not at the top
    document_tools.register(server, caller_for)
    return server
