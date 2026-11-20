"""The document tool surface: the calling user's own converted documents.

A document is a ``documents`` row (the uploaded PDF/image sits in ``directus_files``,
the hub's result in ``markdown``). Like the dictation tools, every Directus call runs
with the caller's token, so Directus' ``user`` policy decides which rows exist.
Writes return metadata only, never Markdown. ``delete_document`` deletes the row the
way the app does: the uploaded file stays in ``directus_files`` (the ``user`` role has
no delete permission there).
"""
from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .server import (
    _UUID, MAX_LIST_LIMIT, MAX_TEXT_CHUNK_CHARS, CallerFactory, Cursor, _as_caller, _decode_cursor,
    _encode_cursor,
)

_META_FIELDS = ["id", "title", "date_created", "date_updated", "status"]

DocumentId = Annotated[str, Field(pattern=_UUID, description="Document id from list_documents.")]
Limit = Annotated[int, Field(default=20, ge=1, le=MAX_LIST_LIMIT, description="How many documents to return (1-50).")]


def _meta(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row.get("title"),
        "created": row.get("date_created"),
        "updated": row.get("date_updated"),
        "status": row.get("status"),
    }


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    markdown = row.get("markdown")
    return {**_meta(row), "markdown_length": None if markdown is None else len(markdown)}


def _cursor(cursor: str) -> tuple[str, int]:
    try:
        return _decode_cursor(cursor)
    except ToolError:
        raise ToolError("cursor must be the next_cursor of an earlier list_documents page") from None


def register(server: MCPServer, caller_for: CallerFactory) -> None:
    """Add the document tools to ``server``."""

    @server.tool()
    async def list_documents(ctx: Context, limit: Limit = 20, cursor: Cursor = None) -> dict[str, Any]:
        """List the user's documents (converted PDFs and images), newest first: id,
        title, created, updated, status and Markdown length in characters (no text).
        Pass next_cursor as cursor for the following page; null means no more.
        Titles are sent to the model of the client that calls this tool."""
        caller = caller_for(ctx)

        async def call() -> tuple[dict[str, Any], list[str]]:
            after, skip = None, 0
            query_filter = None
            if cursor is not None:
                after, skip = _cursor(cursor)
                query_filter = {"date_created": {"_lte": after}}
            rows = await caller.directus.items(
                "documents", fields=[*_META_FIELDS, "markdown"], sort=["-date_created", "-id"],
                limit=limit + 1, offset=skip, filter=query_filter)
            page = rows[:limit]
            next_cursor = None
            if len(rows) > limit:
                last = page[-1]["date_created"]
                same = sum(1 for row in page if row["date_created"] == last)
                # Same tie rule as list_dictations: a page made only of rows from the
                # cursor's instant continues its count.
                next_cursor = _encode_cursor(last, same + (skip if same == len(page) and last == after else 0))
            result = {"documents": [_summary(row) for row in page], "next_cursor": next_cursor}
            return result, [row["id"] for row in page]

        return await _as_caller("list_documents", caller, call, noun="document")

    @server.tool()
    async def get_document(
        ctx: Context,
        id: DocumentId,
        offset: Annotated[int, Field(default=0, ge=0, description="First character of the Markdown range.")] = 0,
        limit: Annotated[int, Field(default=MAX_TEXT_CHUNK_CHARS, ge=1, le=MAX_TEXT_CHUNK_CHARS,
                                    description="Characters to return (at most 32768).")] = MAX_TEXT_CHUNK_CHARS,
    ) -> dict[str, Any]:
        """Read one document: its metadata and one range of its Markdown (the converted
        text the app shows). Follow next_offset until it is null; total_chars is the
        whole length. The Markdown is sent to the model of the client that calls this tool."""
        caller = caller_for(ctx)

        async def call() -> tuple[dict[str, Any], list[str]]:
            row = await caller.directus.item(
                "documents", id, fields=[*_META_FIELDS, "error_message", "markdown", "pages"])
            markdown = row.get("markdown") or ""
            end = min(offset + limit, len(markdown))
            pages = row.get("pages")
            result = {
                **_summary(row),
                "error_message": row.get("error_message"),
                "page_count": len(pages) if isinstance(pages, list) else None,
                "markdown": markdown[offset:end],
                "offset": offset,
                "next_offset": end if end < len(markdown) else None,
                "total_chars": len(markdown),
            }
            return result, [id]

        return await _as_caller("get_document", caller, call, noun="document")

    @server.tool()
    async def rename_document(
        ctx: Context,
        id: DocumentId,
        title: Annotated[str, Field(min_length=1, max_length=255, description="The new title.")],
    ) -> dict[str, Any]:
        """Rename one of the user's documents. Returns the document's metadata (id,
        title, created, updated, status), no Markdown. The title is
        sent to the model of the client that calls this tool."""
        caller = caller_for(ctx)

        async def call() -> tuple[dict[str, Any], list[str]]:
            new_title = title.strip()
            if not new_title:
                raise ToolError("title must not be blank")
            row = await caller.directus.update("documents", id, {"title": new_title}, fields=_META_FIELDS)
            return _meta(row), [id]

        return await _as_caller("rename_document", caller, call, noun="document")

    @server.tool()
    async def delete_document(
        ctx: Context,
        id: DocumentId,
        confirm: Annotated[bool, Field(default=False, description="Must be true: deleting cannot be undone.")] = False,
    ) -> dict[str, Any]:
        """Delete one of the user's documents and its converted Markdown, as the app's
        delete button does. Cannot be undone; nothing happens unless confirm is true.
        Returns the deleted document's id and title, which are
        sent to the model of the client that calls this tool."""
        caller = caller_for(ctx)

        async def call() -> tuple[dict[str, Any], list[str]]:
            if confirm is not True:
                raise ToolError("confirm_required: pass confirm=true to delete; this cannot be undone. "
                                "Nothing was deleted.")
            row = await caller.directus.item("documents", id, fields=["id", "title"])
            await caller.directus.delete("documents", id)
            return {"id": row["id"], "title": row.get("title"), "deleted": True}, [id]

        return await _as_caller("delete_document", caller, call, noun="document")
