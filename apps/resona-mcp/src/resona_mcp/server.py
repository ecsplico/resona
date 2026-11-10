"""The Resona MCP tool surface, shared by the stdio and Streamable HTTP servers.

Every tool talks to the hub through ``resona_client.ResonaClient``; the hub derives the
organization from the client's bearer token, so the MCP layer holds no tenant state.
Submissions return immediately; agents poll ``get_job`` and page through ``get_result``.
No tool mutates settings or profiles, and none touches credentials or worker enrollment.
``postprocess_text`` re-runs a stored postprocess profile on text without an engine.
"""
import base64
import binascii
import hashlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, TypeVar, Union

import anyio.to_thread
import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field
from resona_client.client import (
    AudioOptions,
    DocumentOptions,
    JobView,
    ResonaAPIError,
    ResonaClient,
)
from resona_job_core import MAX_INSTRUCTIONS_CHARS  # resona-client's contract package

from .paths import PathRejected, looks_like_url, resolve_input_path

log = logging.getLogger(__name__)

MAX_UPLOAD_CONTENT_BYTES = 8 * 1024 * 1024
MAX_RESULT_CHUNK_CHARS = 32_768
# The hub's default text upload cap (RESONA_MAX_TEXT_UPLOAD_BYTES): larger text gets 413.
MAX_TEXT_UPLOAD_BYTES = 2 * 1024 * 1024
# Largest base64 text that can decode to MAX_UPLOAD_CONTENT_BYTES (padding included).
_MAX_BASE64_CHARS = 4 * -(-MAX_UPLOAD_CONTENT_BYTES // 3)
_UPLOAD_ID = re.compile(r"^upl_[0-9a-f]{24}$")

TOOL_NAMES = (
    "transcribe_audio",
    "convert_document",
    "postprocess_text",
    "get_job",
    "get_result",
    "cancel_job",
    "list_engines",
    "get_settings",
    "upload_content",
    "create_upload_url",
)

INSTRUCTIONS = (
    "Resona runs private audio transcription and document OCR→Markdown jobs on the "
    "organization's approved workers. Flow: get an upload_id (create_upload_url and send "
    "the file with curl from your shell; upload_content only for tiny files you can pass "
    "verbatim with their sha256; on a local stdio server, a path under RESONA_MCP_ROOT) → "
    "transcribe_audio / convert_document (returns a job_id immediately) → poll get_job "
    "until status is completed/failed/cancelled → page through get_result with "
    "next_offset until it is null. To re-run a stored postprocess profile on an existing "
    "transcript or text file without re-transcribing, use postprocess_text with the "
    "earlier job_id (or a text upload_id) and a profile name; it is a job like the others. "
    "Jobs are private by default; the hub refuses anything its policy does not allow."
)

ClientFactory = Callable[[Context], ResonaClient]
T = TypeVar("T")


@dataclass(frozen=True)
class InputPolicy:
    """Where job input may come from. Remote servers never accept local paths."""

    allow_paths: bool
    root: Optional[Path] = None


Profile = Annotated[
    Optional[str],
    Field(default=None, max_length=64, description="Execution profile name, e.g. 'private-auto', 'mac-private', 'cpu-private'. Default: the organization's default."),
]
Language = Annotated[
    Optional[str],
    Field(default=None, pattern=r"^[a-z]{2,3}$", description="Language code, e.g. 'de' or 'en'. Default: the organization's language."),
]
PostprocessProfile = Annotated[
    Optional[str],
    Field(default=None, max_length=64, description="Name of a stored postprocess profile on the hub."),
]
UserInstructions = Annotated[
    Optional[str],
    Field(default=None, min_length=1, max_length=MAX_INSTRUCTIONS_CHARS,
          description="Your instructions for the postprocess profile's LLM steps, e.g. names to keep or "
                      "what to clean up. Sent as the user message; the profile's prompt stays the system "
                      "message. Needs a profile with an LLM step."),
]
Private = Annotated[
    bool,
    Field(default=True, description="Keep processing on approved private workers (default). false requires the organization to allow cloud processing."),
]
UploadId = Annotated[
    Optional[str],
    Field(default=None, description="upload_id from create_upload_url or upload_content."),
]
LocalPath = Annotated[
    Optional[str],
    Field(default=None, description="Local stdio servers only: file path under RESONA_MCP_ROOT. Remote servers reject it."),
]
IdempotencyKey = Annotated[
    Optional[str],
    Field(default=None, max_length=200, description="Reuse the same key when retrying a submission so the hub returns the same job."),
]
JobId = Annotated[int, Field(ge=1, description="Job id returned by transcribe_audio, convert_document or postprocess_text.")]
SourceJobId = Annotated[
    Optional[int],
    Field(default=None, ge=1, description="A completed job whose result text to postprocess: its unpolished Markdown (raw_markdown) when it has one, else its Markdown."),
]
RequiredProfile = Annotated[
    Optional[str],
    Field(default=None, max_length=64, description="Required. Name of a stored postprocess profile on the hub."),
]


def _api_error(exc: ResonaAPIError) -> ToolError:
    label = exc.code or "error"
    return ToolError(f"hub returned {exc.status} {label}" + (f": {exc.message}" if exc.message else ""))


async def _run(fn: Callable[[], T]) -> T:
    """Run blocking hub I/O off the event loop and turn hub errors into tool errors."""
    try:
        return await anyio.to_thread.run_sync(fn)
    except ResonaAPIError as exc:
        raise _api_error(exc) from None
    except httpx.TransportError:
        raise ToolError("the Resona hub is unreachable") from None


def _job_summary(job: JobView) -> dict[str, Any]:
    keys = ("job_id", "kind", "status", "queue_reason", "private", "execution_profile",
            "candidate", "attempt", "error", "result_available", "created_at",
            "updated_at", "deadline_at")
    return {key: job.raw.get(key) for key in keys}


def build_server(client_for: ClientFactory, inputs: InputPolicy) -> MCPServer:
    """Create the MCP server; ``client_for(ctx)`` returns a hub client for the caller."""
    server = MCPServer("resona", instructions=INSTRUCTIONS)

    def resolve_source(upload_id: Optional[str], path: Optional[str]) -> tuple[Optional[str], Optional[Path]]:
        if (upload_id is None) == (path is None):
            raise ToolError("pass exactly one of upload_id or path")
        if upload_id is not None:
            if not _UPLOAD_ID.match(upload_id):
                raise ToolError("upload_id must be an id returned by create_upload_url or upload_content")
            return upload_id, None
        assert path is not None
        if not inputs.allow_paths:
            what = "URLs" if looks_like_url(path) else "local paths"
            raise ToolError(f"this remote server does not accept {what}; use create_upload_url")
        try:
            return None, resolve_input_path(path, inputs.root)
        except PathRejected as exc:
            raise ToolError(str(exc)) from None

    async def submit(
        ctx: Context,
        *,
        kind: Literal["audio", "document"],
        upload_id: Optional[str],
        path: Optional[str],
        options: Union[AudioOptions, DocumentOptions, None],
        execution_profile: Optional[str],
        language: Optional[str],
        profile: Optional[str],
        instructions: Optional[str],
        private: bool,
        idempotency_key: Optional[str],
    ) -> dict[str, Any]:
        upload_id, local = resolve_source(upload_id, path)
        client = client_for(ctx)

        def call() -> dict[str, Any]:
            with client:
                target = upload_id or client.upload(local).upload_id  # type: ignore[arg-type]
                ref = client.submit_upload(
                    target,
                    kind=kind,
                    execution_profile=execution_profile,
                    language=language,
                    profile=profile,
                    instructions=instructions,
                    options=options,
                    private=private,
                    idempotency_key=idempotency_key,
                )
            return {"job_id": ref.job_id, "status": ref.status, "private": private}

        return await _run(call)

    @server.tool()
    async def transcribe_audio(
        ctx: Context,
        upload_id: UploadId = None,
        path: LocalPath = None,
        execution_profile: Profile = None,
        language: Language = None,
        profile: PostprocessProfile = None,
        instructions: UserInstructions = None,
        options: Optional[AudioOptions] = None,
        private: Private = True,
        idempotency_key: IdempotencyKey = None,
    ) -> dict[str, Any]:
        """Submit an audio transcription job; returns {job_id, status, private} immediately."""
        return await submit(
            ctx, kind="audio", upload_id=upload_id, path=path, options=options,
            execution_profile=execution_profile, language=language, profile=profile,
            instructions=instructions, private=private, idempotency_key=idempotency_key,
        )

    @server.tool()
    async def convert_document(
        ctx: Context,
        upload_id: UploadId = None,
        path: LocalPath = None,
        execution_profile: Profile = None,
        language: Language = None,
        profile: PostprocessProfile = None,
        instructions: UserInstructions = None,
        options: Optional[DocumentOptions] = None,
        private: Private = True,
        idempotency_key: IdempotencyKey = None,
    ) -> dict[str, Any]:
        """Submit a PDF/image/Word DOCX → Markdown conversion job; returns {job_id, status, private} immediately. A DOCX takes no OCR or page options."""
        return await submit(
            ctx, kind="document", upload_id=upload_id, path=path, options=options,
            execution_profile=execution_profile, language=language, profile=profile,
            instructions=instructions, private=private, idempotency_key=idempotency_key,
        )

    def read_job_text(client: ResonaClient, job_id: int) -> tuple[str, Optional[str]]:
        """An earlier result's unpolished Markdown, read in bounded hub ranges."""
        too_large = f"job {job_id}'s result exceeds 2 MiB (hub text upload cap)"
        parts: list[str] = []
        size, offset, language = 0, 0, None
        while True:
            chunk = client.get_result_markdown(
                job_id, offset=offset, limit=MAX_RESULT_CHUNK_CHARS, source="raw")
            if chunk.source is None:
                raise ToolError("hub does not support source=raw; upgrade the hub")
            if chunk.total_chars > MAX_TEXT_UPLOAD_BYTES:
                raise ToolError(too_large)
            language = chunk.language
            parts.append(chunk.text)
            size += len(chunk.text.encode("utf-8"))
            if size > MAX_TEXT_UPLOAD_BYTES:
                raise ToolError(too_large)
            if chunk.next_offset is None:
                break
            if chunk.next_offset <= offset:
                raise ToolError(f"the hub returned an inconsistent range for job {job_id}")
            offset = chunk.next_offset
        text = "".join(parts)
        if not text.strip():
            raise ToolError(f"job {job_id}'s result has no text to postprocess")
        return text, language

    @server.tool()
    async def postprocess_text(
        ctx: Context,
        profile: RequiredProfile = None,
        instructions: UserInstructions = None,
        upload_id: UploadId = None,
        path: LocalPath = None,
        job_id: SourceJobId = None,
        language: Language = None,
        private: Private = True,
        idempotency_key: IdempotencyKey = None,
    ) -> dict[str, Any]:
        """Run a stored postprocess profile on text (no engine); returns {job_id, status,
        private} immediately.

        Pass exactly one source: upload_id (a UTF-8 text/Markdown upload), path (local
        stdio servers only) or job_id (re-run on an earlier job's result, e.g. to polish a
        transcript again without re-transcribing). With job_id the language defaults to
        that job's, and private=false is refused when that job is private. Fetch the
        output with get_job / get_result like any job."""
        if not profile:
            raise ToolError("profile_required: postprocess_text needs the name of a stored postprocess profile")
        if job_id is not None:
            if upload_id is not None or path is not None:
                raise ToolError("pass exactly one of upload_id, path or job_id")
            local: Optional[Path] = None
        else:
            upload_id, local = resolve_source(upload_id, path)
        client = client_for(ctx)

        def call() -> dict[str, Any]:
            lang = language
            with client:
                if job_id is not None:
                    if not private and client.get_job(job_id).private:
                        raise ToolError(
                            f"job {job_id} is private; its text cannot go to non-private "
                            "processing (use private=true)")
                    text, source_language = read_job_text(client, job_id)
                    lang = lang or source_language
                    target = client.upload_bytes(
                        f"job-{job_id}.{profile}.md", text.encode("utf-8")).upload_id
                else:
                    target = upload_id or client.upload(local).upload_id  # type: ignore[arg-type]
                ref = client.submit_upload(
                    target,
                    kind="text",
                    language=lang,
                    profile=profile,
                    instructions=instructions,
                    private=private,
                    idempotency_key=idempotency_key,
                )
            return {"job_id": ref.job_id, "status": ref.status, "private": private}

        return await _run(call)

    @server.tool()
    async def get_job(ctx: Context, job_id: JobId) -> dict[str, Any]:
        """Job status: pending (with queue_reason), processing, completed, failed or cancelled."""
        client = client_for(ctx)

        def call() -> dict[str, Any]:
            with client:
                return _job_summary(client.get_job(job_id))

        return await _run(call)

    @server.tool()
    async def get_result(
        ctx: Context,
        job_id: JobId,
        offset: Annotated[int, Field(ge=0, description="Character offset into the Markdown.")] = 0,
        limit: Annotated[int, Field(ge=1, le=MAX_RESULT_CHUNK_CHARS, description="Maximum characters to return.")] = MAX_RESULT_CHUNK_CHARS,
    ) -> dict[str, Any]:
        """One chunk of a completed job's Markdown. Concatenating chunks from offset 0
        while next_offset is not null reproduces the full Markdown exactly."""
        client = client_for(ctx)

        def call() -> dict[str, Any]:
            with client:
                chunk = client.get_result_markdown(job_id, offset=offset, limit=limit)
            return {
                "job_id": job_id,
                "kind": chunk.kind,
                "language": chunk.language,
                "markdown": chunk.text,
                "offset": chunk.offset,
                "next_offset": chunk.next_offset,
                "total_chars": chunk.total_chars,
                "page_count": chunk.page_count,
                "warnings": list(chunk.warnings),
                "provenance": dict(chunk.provenance),
            }

        return await _run(call)

    @server.tool()
    async def cancel_job(ctx: Context, job_id: JobId) -> dict[str, Any]:
        """Request cancellation; a worker's later output for the job is rejected."""
        client = client_for(ctx)

        def call() -> dict[str, Any]:
            with client:
                return _job_summary(client.cancel_job(job_id))

        return await _run(call)

    @server.tool()
    async def list_engines(ctx: Context) -> dict[str, Any]:
        """Execution profiles (approved engine/model/accelerator candidates per job kind)
        and the hub's synchronous engine catalogue."""
        client = client_for(ctx)

        def call() -> dict[str, Any]:
            with client:
                settings = client.get_settings().settings
                catalogue = client.list_engines()
            return {
                "default_execution_profile": settings.get("default_execution_profile"),
                "execution_profiles": settings.get("execution_profiles", {}),
                "engines": catalogue.get("engines", []),
            }

        return await _run(call)

    @server.tool()
    async def get_settings(ctx: Context) -> dict[str, Any]:
        """The organization's current (non-secret) settings revision. Read-only."""
        client = client_for(ctx)

        def call() -> dict[str, Any]:
            with client:
                doc = client.get_settings()
            return {"organization_id": doc.organization_id, "revision": doc.revision, "settings": doc.settings}

        return await _run(call)

    @server.tool()
    async def upload_content(
        ctx: Context,
        filename: Annotated[str, Field(min_length=1, max_length=255, description="Name hint, e.g. 'memo.wav' or 'scan.pdf'.")],
        content_base64: Annotated[str, Field(description="Base64 file content, at most 8 MiB decoded.")],
        sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", description="Lowercase SHA-256 hex of the original file bytes (e.g. from `sha256sum`). Content that does not match is rejected.")],
    ) -> dict[str, Any]:
        """Upload a small file inline (≤ 8 MiB decoded); returns {upload_id, sha256, size} for one job.

        Base64 that a model copies by hand is easily altered, so only use this for
        tiny files. If you can run shell commands, use create_upload_url instead."""
        if len(content_base64) > _MAX_BASE64_CHARS:
            raise ToolError("content exceeds the 8 MiB upload_content limit")
        try:
            data = base64.b64decode(content_base64, validate=True)
        except (binascii.Error, ValueError):
            raise ToolError("content_base64 is not valid base64") from None
        if len(data) > MAX_UPLOAD_CONTENT_BYTES:
            raise ToolError("content exceeds the 8 MiB upload_content limit")
        if not data:
            raise ToolError("content is empty")
        if hashlib.sha256(data).hexdigest() != sha256:
            raise ToolError(
                "decoded content does not match sha256: the base64 was altered on the way; "
                "use create_upload_url and send the file with curl"
            )
        name = Path(filename).name or "upload"
        client = client_for(ctx)

        def call() -> dict[str, Any]:
            with client:
                up = client.upload_bytes(name, data)
            return {"upload_id": up.upload_id, "sha256": up.sha256, "size": up.size}

        return await _run(call)

    @server.tool()
    async def create_upload_url(ctx: Context) -> dict[str, Any]:
        """Get a single-use upload link (valid 10 minutes) for one file of any supported size.

        Run the returned `curl` command in your shell with the file path filled in; it
        prints {upload_id, sha256, size}. The file's bytes go straight to the hub, not
        through this conversation. Use the upload_id with transcribe_audio or
        convert_document."""
        client = client_for(ctx)

        def call() -> dict[str, Any]:
            with client:
                link = client.create_upload_link()
            header = f"{link.token_header}: {link.token}"
            return {
                "upload_url": link.upload_url,
                "token_header": link.token_header,
                "token": link.token,
                "expires_at": link.expires_at,
                "max_bytes": link.max_bytes,
                "curl": f"curl -sS -X POST -H '{header}' -F 'file=@<PATH>' {link.upload_url}",
            }

        return await _run(call)

    return server


__all__ = ["InputPolicy", "build_server", "TOOL_NAMES", "MAX_UPLOAD_CONTENT_BYTES",
           "MAX_RESULT_CHUNK_CHARS", "MAX_TEXT_UPLOAD_BYTES"]
