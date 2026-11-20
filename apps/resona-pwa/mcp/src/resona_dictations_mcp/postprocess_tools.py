"""Postprocess tools: the app's "Nachbearbeiten" dialog (run a profile, restore the raw transcript).

The service never calls the Resona hub. ``postprocess_dictation`` writes the same
request onto the transcript row as the app (``postprocessRequest`` in
``app/utils/postprocess.ts``); the project worker picks it up, runs the hub text job and
writes the result back, as for a request made in the app. ``restore_raw_transcript``
writes the app's ``rawRestore``. ``shared/dictationEdit.cases.json`` pins both sides to
the same writes; ``PROFILES`` is ``shared/postprocessProfiles.json`` (the image is built
from ``mcp/`` alone, so it is a copy, checked by the tests).
"""
from typing import Annotated, Any, Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .edit_tools import postprocess_running
from .server import _SUMMARY_FIELDS, CallerFactory, DictationId, _as_caller, _summaries, text_sha256

# The profiles the app offers under "Nachbearbeiten" (shared/postprocessProfiles.json).
# `instructions`: the profile has an llm/extract step, so the hub accepts instructions.
PROFILES: tuple[dict[str, Any], ...] = (
    {"name": "transcript-markdown-de", "instructions": False,
     "description": "German dictation commands to Markdown (rules only, no language model): "
                    "headings, paragraphs, punctuation."},
    {"name": "transcript-polish-de", "instructions": True,
     "description": "The same rules, then a language model fixes capitalization, punctuation and "
                    "whitespace only; no word changes."},
)
# Same cap as the hub (resona-job-core MAX_INSTRUCTIONS_CHARS).
MAX_INSTRUCTIONS_CHARS = 8000

_TRANSCRIPT_FIELDS = ["id", "text", "raw_text", "applied_profile", "processed_text", "postprocess_status"]

Confirm = Annotated[
    bool,
    Field(default=False, description="Must be true when the text was edited since the profile ran "
                                     "(those edits are replaced); ask the user first."),
]


def postprocess_input(transcript: dict[str, Any]) -> str:
    """Same rule as `postprocessInput` in the app: `raw_text`, else (never re-processed) `text`."""
    raw = transcript.get("raw_text")
    return raw if raw is not None else (transcript.get("text") or "")


def postprocess_request(profile: str, instructions: Optional[str] = None) -> dict[str, Any]:
    """The app's `postprocessRequest`: the project worker runs `profile` with `instructions`."""
    return {"postprocess_status": "pending", "postprocess_profile": profile, "postprocess_instructions": instructions}


def raw_restore(transcript: dict[str, Any]) -> dict[str, Any]:
    """The app's `rawRestore`: the raw transcript becomes the text again."""
    return {"text": postprocess_input(transcript), "raw_text": None, "applied_profile": None,
            "processed_text": None, "postprocess_status": None}


def edited_since_profile(transcript: dict[str, Any]) -> bool:
    """The app's `editedSinceProfile`: a profile was applied and `text` differs from its output."""
    return transcript.get("applied_profile") is not None and \
        (transcript.get("text") or "") != transcript.get("processed_text")


def register(server: MCPServer, caller_for: CallerFactory) -> None:
    """Add list_profiles, postprocess_dictation and restore_raw_transcript to ``server``."""

    async def own_transcript(directus, id: str) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
        row = await directus.item("recordings", id, fields=_SUMMARY_FIELDS)
        rows = await directus.items("transcripts", fields=_TRANSCRIPT_FIELDS,
                                    filter={"recording": {"_eq": id}}, limit=1)
        return row, (rows[0] if rows else None)

    def confirmed(transcript: dict[str, Any], confirm: bool) -> None:
        if edited_since_profile(transcript) and confirm is not True:
            raise ToolError("confirm_required: the text was edited since the profile ran and those edits "
                            "would be replaced; ask the user, then pass confirm=true. Nothing was written.")

    @server.tool()
    async def list_profiles() -> dict[str, Any]:
        """List the postprocess profiles postprocess_dictation accepts: this app's
        catalogue (name, description, whether it takes instructions), not the hub's live
        profile list, which this server never reads. Names and descriptions are
        sent to the model of the client that calls this tool."""
        return {"profiles": [dict(p) for p in PROFILES]}

    @server.tool()
    async def postprocess_dictation(
        ctx: Context,
        id: DictationId,
        profile: Annotated[str, Field(min_length=1, max_length=255, description="A profile name from list_profiles.")],
        instructions: Annotated[
            Optional[str],
            Field(default=None, min_length=1, max_length=MAX_INSTRUCTIONS_CHARS,
                  description="Instructions for the profile's language model step (only profiles whose "
                              "list_profiles entry has instructions=true take them)."),
        ] = None,
        confirm: Confirm = False,
    ) -> dict[str, Any]:
        """Run a postprocess profile on one dictation, as the app's "Nachbearbeiten"
        does: the profile works on the raw transcript, its output becomes the text and
        the raw transcript is kept (restore_raw_transcript undoes it). Returns at once
        with postprocess_status 'pending'; the app's worker runs the profile, so poll
        get_dictation until postprocess_status is 'done' or 'error'. An unknown profile
        (see list_profiles) fails with 'unknown_profile' and writes nothing; 'busy'
        while a profile already runs; 'confirm_required' when the text was edited since
        the last profile ran. Returns the dictation's metadata (no text); it is
        sent to the model of the client that calls this tool."""
        caller = caller_for(ctx)

        async def call() -> tuple[dict[str, Any], list[str]]:
            entry = next((p for p in PROFILES if p["name"] == profile), None)
            if entry is None:
                names = ", ".join(p["name"] for p in PROFILES)
                raise ToolError(f"unknown_profile: no profile named {profile!r}; use one of: {names}. "
                                "Nothing was written.")
            if instructions is not None and not entry["instructions"]:
                raise ToolError(f"invalid_instructions: profile {profile!r} has no language model step and "
                                "takes no instructions. Nothing was written.")
            directus = caller.directus
            row, transcript = await own_transcript(directus, id)
            if transcript is None or not postprocess_input(transcript).strip():
                raise ToolError("no_transcript: this dictation has no transcript text to postprocess")
            if postprocess_running(transcript):
                raise ToolError("busy: a postprocess profile is running on this dictation; try again when it is done")
            confirmed(transcript, confirm)
            await directus.update("transcripts", str(transcript["id"]), postprocess_request(profile, instructions),
                                  fields=["id"])
            result = (await _summaries(directus, [row]))[0]
            return {**result, "postprocess_status": "pending", "postprocess_profile": profile}, [id]

        return await _as_caller("postprocess_dictation", caller, call)

    @server.tool()
    async def restore_raw_transcript(ctx: Context, id: DictationId, confirm: Confirm = False) -> dict[str, Any]:
        """Undo a postprocess profile on one dictation, as the app's "Rohtranskript
        wiederherstellen" does: the raw transcript becomes the text again and the
        profile's output is dropped. 'nothing_to_restore' when no profile was applied,
        'busy' while one runs, 'confirm_required' when the text was edited since the
        profile ran (those edits are lost). Returns the dictation's metadata and the
        new text_sha256 (no text); that metadata is
        sent to the model of the client that calls this tool."""
        caller = caller_for(ctx)

        async def call() -> tuple[dict[str, Any], list[str]]:
            directus = caller.directus
            row, transcript = await own_transcript(directus, id)
            if transcript is not None and postprocess_running(transcript):
                raise ToolError("busy: a postprocess profile is running on this dictation; try again when it is done")
            if transcript is None or transcript.get("applied_profile") is None:
                raise ToolError("nothing_to_restore: no postprocess profile was applied to this dictation")
            confirmed(transcript, confirm)
            patch = raw_restore(transcript)
            await directus.update("transcripts", str(transcript["id"]), patch, fields=["id"])
            result = (await _summaries(directus, [row]))[0]
            return {**result, "text_sha256": text_sha256(patch["text"])}, [id]

        return await _as_caller("restore_raw_transcript", caller, call)
