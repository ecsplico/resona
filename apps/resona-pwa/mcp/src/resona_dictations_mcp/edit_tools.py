"""Write tools: edit text, rename, review status, delete, each as the app does it.

The fields each tool writes are the app's (`app/utils/dictationEdit.ts`, the transcript
editor `useTranscriptEdit.ts`, `deleteRecording` in `app/data/directusStore.ts`);
`shared/dictationEdit.cases.json` pins both sides to the same writes. Every write runs
with the caller's token, like the reads, and returns metadata only, never text.
"""
from typing import Annotated, Any, Literal, Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .server import _SUMMARY_FIELDS, CallerFactory, DictationId, _as_caller, _summaries, text_sha256

Sha256 = Annotated[
    Optional[str],
    Field(default=None, pattern=r"^[0-9a-fA-F]{64}$",
          description="text_sha256 from get_dictation: the edit is refused with 'conflict' when the "
                      "text changed since (e.g. the user edited it in the app)."),
]


def postprocess_running(transcript: Optional[dict[str, Any]]) -> bool:
    """Same rule as `postprocessRunning` in the app: the text is locked while a profile runs."""
    return (transcript or {}).get("postprocess_status") in ("pending", "processing")


def rename_patch(title: str) -> dict[str, Any]:
    """The app's rename: the trimmed title, and it is no longer the automatic placeholder."""
    return {"title": title.strip(), "title_auto": False}


def review_status_patch(status: str) -> dict[str, Any]:
    return {"review_status": status}


def register(server: MCPServer, caller_for: CallerFactory) -> None:
    """Add the four write tools to ``server``."""

    async def metadata(directus, row: dict[str, Any]) -> dict[str, Any]:
        return (await _summaries(directus, [row]))[0]

    @server.tool()
    async def update_dictation_text(
        ctx: Context,
        id: DictationId,
        text: Annotated[str, Field(max_length=2_000_000, description="The complete new text of the dictation.")],
        expected_sha256: Sha256 = None,
    ) -> dict[str, Any]:
        """Replace the text of one dictation (the text the app shows and edits) with
        `text`. The raw engine transcript and a profile's output stay as they are, as
        when the user edits in the app. Pass expected_sha256 (text_sha256 from
        get_dictation) so a text the user changed meanwhile is not overwritten: then
        the tool fails with 'conflict' and writes nothing. Refused with 'busy' while a
        postprocess profile runs. Returns the dictation's metadata and the new
        text_sha256, not the text; that metadata is
        sent to the model of the client that calls this tool."""
        caller = caller_for(ctx)

        async def call() -> tuple[dict[str, Any], list[str]]:
            directus = caller.directus
            row = await directus.item("recordings", id, fields=_SUMMARY_FIELDS)
            rows = await directus.items(
                "transcripts", fields=["id", "text", "postprocess_status"],
                filter={"recording": {"_eq": id}}, limit=1)
            transcript = rows[0] if rows else None
            if postprocess_running(transcript):
                raise ToolError("busy: a postprocess profile is running on this dictation; try again when it is done")
            current = (transcript or {}).get("text") or ""
            if expected_sha256 is not None and expected_sha256.lower() != text_sha256(current):
                raise ToolError("conflict: the text changed since it was read (expected_sha256 does not match); "
                                "read it again with get_dictation. Nothing was written.")
            if transcript is None:
                # As the app does for a dictation that has no transcript yet.
                await directus.create("transcripts", {"recording": id, "text": text}, fields=["id"])
            else:
                await directus.update("transcripts", str(transcript["id"]), {"text": text}, fields=["id"])
            return {**await metadata(directus, row), "text_sha256": text_sha256(text)}, [id]

        return await _as_caller("update_dictation_text", caller, call)

    @server.tool()
    async def rename_dictation(
        ctx: Context,
        id: DictationId,
        title: Annotated[str, Field(min_length=1, max_length=255, description="The new title.")],
    ) -> dict[str, Any]:
        """Rename one dictation. Like a rename in the app, the title then counts as the
        user's own, so it is never replaced by one derived from the transcript.
        Returns the dictation's metadata (no text); it is
        sent to the model of the client that calls this tool."""
        caller = caller_for(ctx)

        async def call() -> tuple[dict[str, Any], list[str]]:
            row = await caller.directus.update("recordings", id, rename_patch(title), fields=_SUMMARY_FIELDS)
            return await metadata(caller.directus, row), [id]

        return await _as_caller("rename_dictation", caller, call)

    @server.tool()
    async def set_review_status(
        ctx: Context,
        id: DictationId,
        status: Annotated[Literal["editing", "ready"],
                          Field(description="'ready' releases the dictation, 'editing' takes the release back.")],
    ) -> dict[str, Any]:
        """Set the review status of one dictation, as the app's release switch does.
        Returns the dictation's metadata (no text); it is
        sent to the model of the client that calls this tool."""
        caller = caller_for(ctx)

        async def call() -> tuple[dict[str, Any], list[str]]:
            row = await caller.directus.update("recordings", id, review_status_patch(status), fields=_SUMMARY_FIELDS)
            return await metadata(caller.directus, row), [id]

        return await _as_caller("set_review_status", caller, call)

    @server.tool()
    async def delete_dictation(
        ctx: Context,
        id: DictationId,
        confirm: Annotated[bool, Field(default=False, description="Must be true: deleting cannot be undone.")] = False,
    ) -> dict[str, Any]:
        """Delete one dictation for good, as the app's delete does: the recording with
        its transcript, its corrections and its dictated parts. This cannot be undone.
        Refused unless confirm is true; ask the user first. The audio stays
        unreachable in file storage, as after a delete in the app. Returns the
        deleted dictation's metadata (no text); it is
        sent to the model of the client that calls this tool."""
        caller = caller_for(ctx)

        async def call() -> tuple[dict[str, Any], list[str]]:
            if confirm is not True:
                raise ToolError("confirm_required: pass confirm=true to delete; this cannot be undone. "
                                "Nothing was deleted.")
            row = await caller.directus.item("recordings", id, fields=_SUMMARY_FIELDS)
            result = await metadata(caller.directus, row)
            await caller.directus.delete("recordings", id)
            return {**result, "deleted": True}, [id]

        return await _as_caller("delete_dictation", caller, call)
