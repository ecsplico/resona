"""Tools that create dictations, and the audio link.

Rows are created with the caller's own Directus token, as the app creates them:
- a text dictation is a finished recording (``source`` ``live``, ``status`` ``done``,
  like a saved live dictation) plus its ``transcripts`` row;
- an audio upload goes through a signed single-use link (``links.py``): the bytes never
  pass the model, and the upload creates the recording ``pending`` for the project worker.
No hub call: transcription stays the project worker's job.
"""
import shlex
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .links import Links, TooManyLinks
from .server import CallerFactory, DictationId, _as_caller

MAX_TEXT_CHARS = 200_000

Title = Annotated[
    Optional[str],
    Field(default=None, max_length=200,
          description="Title of the dictation; omit it to get a dated placeholder the app marks as automatic."),
]
Language = Annotated[
    Optional[str],
    Field(default=None, pattern=r"^[a-z]{2,3}$", description="Language code such as 'de' or 'en'; omit for the app default."),
]
Profile = Annotated[
    Optional[str],
    Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
          description="Postprocess profile name for the transcription; omit for the app default."),
]

_NOT_ENABLED = "audio links are not enabled on this server (DICTATIONS_MCP_LINK_SECRET is not set)"


def _dated_title(prefix: str) -> str:
    """The app's placeholder titles (``titleSuggestion.ts``): ``<prefix> DD.MM.YYYY HH:MM``, server local time."""
    return datetime.now().astimezone().strftime(f"{prefix} %d.%m.%Y %H:%M")


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _links(links: Optional[Links]) -> Links:
    if links is None:
        raise ToolError(_NOT_ENABLED)
    return links


def register(server: MCPServer, caller_for: CallerFactory, links: Optional[Links]) -> None:
    """Add the create tools to ``server``; ``links`` None disables the two link tools' use."""

    @server.tool()
    async def create_dictation_from_text(
        ctx: Context,
        text: Annotated[str, Field(min_length=1, max_length=MAX_TEXT_CHARS, description="The dictation's text.")],
        title: Title = None,
        language: Language = None,
    ) -> dict[str, Any]:
        """Create a finished dictation of the user from text: it appears in the app like a
        saved live dictation (status done). Without a title it gets a dated placeholder
        (title_auto true). Returns its id, title, language, status and text length; these
        are sent to the model of the client that calls this tool."""
        caller = caller_for(ctx)

        async def call() -> tuple[dict[str, Any], list[str]]:
            auto = title is None or not title.strip()
            data: dict[str, Any] = {
                "title": _dated_title("Diktat") if auto else title.strip(),
                "title_auto": auto, "source": "live", "status": "done", "duration_seconds": 0,
            }
            if language:
                data["language"] = language
            row = await caller.directus.create_item(
                "recordings", data, fields=["id", "title", "title_auto", "language", "status", "date_created"])
            try:
                await caller.directus.create_item("transcripts", {"recording": row["id"], "text": text}, fields=["id"])
            except Exception:
                # No finished dictation without its text: the user's own row goes again.
                try:
                    await caller.directus.delete_item("recordings", row["id"])
                except Exception:  # noqa: BLE001 - the original failure is the one to report
                    pass
                raise
            return {
                "id": row["id"], "title": row.get("title"), "title_auto": auto, "language": row.get("language"),
                "status": row.get("status"), "created": row.get("date_created"), "text_length": len(text),
            }, [row["id"]]

        return await _as_caller("create_dictation_from_text", caller, call)

    @server.tool()
    async def create_audio_upload(
        ctx: Context,
        filename: Annotated[str, Field(min_length=1, max_length=255,
                                       description="Name (or path) of the local audio file to upload.")],
        language: Language = None,
        profile: Profile = None,
    ) -> dict[str, Any]:
        """Get a single-use upload URL (valid 10 minutes) for an audio file, plus a curl
        command that uploads it, so the audio never passes through the model. Run the
        command where the file is; the upload creates the dictation (id below) as pending
        and the app transcribes it; follow it with get_dictation. WAV, MP3, AAC, Ogg, FLAC,
        MP4/M4A or WebM up to max_bytes. The URL and command are
        sent to the model of the client that calls this tool."""
        caller = caller_for(ctx)

        async def call() -> tuple[dict[str, Any], list[str]]:
            issuer = _links(links)
            recording_id = str(uuid.uuid4())
            try:
                url, expires = issuer.upload_link(
                    user_id=caller.user_id, directus=caller.directus, recording_id=recording_id,
                    filename=filename, title=_dated_title("Aufnahme"), language=language, profile=profile)
            except TooManyLinks:
                raise ToolError("too many open upload links; try again in a few minutes") from None
            return {
                "id": recording_id,
                "upload_url": url,
                "method": "PUT",
                "expires_at": _iso(expires),
                "max_bytes": issuer.max_upload_bytes,
                "single_use": True,
                "curl": f"curl --fail-with-body -T {shlex.quote(filename)} {shlex.quote(url)}",
            }, [recording_id]

        return await _as_caller("create_audio_upload", caller, call)

    @server.tool()
    async def get_audio_link(ctx: Context, id: DictationId) -> dict[str, Any]:
        """Get a link to the audio of one of the user's dictations, valid for 10 minutes
        (anyone holding it can play the audio until then). The link is
        sent to the model of the client that calls this tool."""
        caller = caller_for(ctx)

        async def call() -> tuple[dict[str, Any], list[str]]:
            issuer = _links(links)
            row = await caller.directus.item("recordings", id, fields=["id", "audio_file"])
            if not row.get("audio_file"):
                raise ToolError("this dictation has no audio")
            try:
                url, expires = issuer.audio_link(user_id=caller.user_id, directus=caller.directus, recording_id=id)
            except TooManyLinks:
                raise ToolError("too many open audio links; try again in a few minutes") from None
            return {"id": id, "audio_url": url, "expires_at": _iso(expires)}, [id]

        return await _as_caller("get_audio_link", caller, call)
