"""The Resona MCP tool surface, shared by the stdio and Streamable HTTP servers.

Every tool talks to the hub through ``resona_client.ResonaClient``; the hub derives the
organization from the client's bearer token, so the MCP layer holds no tenant state.
Submissions return immediately; agents poll ``get_job`` and page through ``get_result``.
No tool mutates settings or profiles, and none touches credentials or worker enrollment.
"""
import base64
import binascii
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

from .paths import PathRejected, looks_like_url, resolve_input_path

log = logging.getLogger(__name__)

MAX_UPLOAD_CONTENT_BYTES = 8 * 1024 * 1024
MAX_RESULT_CHUNK_CHARS = 20_000
# Largest base64 text that can decode to MAX_UPLOAD_CONTENT_BYTES (padding included).
_MAX_BASE64_CHARS = 4 * -(-MAX_UPLOAD_CONTENT_BYTES // 3)
_UPLOAD_ID = re.compile(r"^upl_[0-9a-f]{24}$")

TOOL_NAMES = (
    "transcribe_audio",
    "convert_document",
    "get_job",
    "get_result",
    "cancel_job",
    "list_engines",
    "get_settings",
    "upload_content",
)

INSTRUCTIONS = (
    "Resona runs private audio transcription and document OCR→Markdown jobs on the "
    "organization's approved workers. Flow: upload_content (or, on a local stdio server, "
    "pass a path under RESONA_MCP_ROOT) → transcribe_audio / convert_document (returns a "
    "job_id immediately) → poll get_job until status is completed/failed/cancelled → page "
    "through get_result with next_offset until it is null. Jobs are private by default; "
    "the hub refuses anything its policy does not allow."
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
Private = Annotated[
    bool,
    Field(default=True, description="Keep processing on approved private workers (default). false requires the organization to allow cloud processing."),
]
UploadId = Annotated[
    Optional[str],
    Field(default=None, description="upload_id from upload_content."),
]
LocalPath = Annotated[
    Optional[str],
    Field(default=None, description="Local stdio servers only: file path under RESONA_MCP_ROOT. Remote servers reject it."),
]
IdempotencyKey = Annotated[
    Optional[str],
    Field(default=None, max_length=200, description="Reuse the same key when retrying a submission so the hub returns the same job."),
]
JobId = Annotated[int, Field(ge=1, description="Job id returned by transcribe_audio or convert_document.")]


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
                raise ToolError("upload_id must be an id returned by upload_content")
            return upload_id, None
        assert path is not None
        if not inputs.allow_paths:
            what = "URLs" if looks_like_url(path) else "local paths"
            raise ToolError(f"this remote server does not accept {what}; use upload_content")
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
        options: Optional[AudioOptions] = None,
        private: Private = True,
        idempotency_key: IdempotencyKey = None,
    ) -> dict[str, Any]:
        """Submit an audio transcription job; returns {job_id, status, private} immediately."""
        return await submit(
            ctx, kind="audio", upload_id=upload_id, path=path, options=options,
            execution_profile=execution_profile, language=language, profile=profile,
            private=private, idempotency_key=idempotency_key,
        )

    @server.tool()
    async def convert_document(
        ctx: Context,
        upload_id: UploadId = None,
        path: LocalPath = None,
        execution_profile: Profile = None,
        language: Language = None,
        profile: PostprocessProfile = None,
        options: Optional[DocumentOptions] = None,
        private: Private = True,
        idempotency_key: IdempotencyKey = None,
    ) -> dict[str, Any]:
        """Submit a PDF/image → Markdown conversion job; returns {job_id, status, private} immediately."""
        return await submit(
            ctx, kind="document", upload_id=upload_id, path=path, options=options,
            execution_profile=execution_profile, language=language, profile=profile,
            private=private, idempotency_key=idempotency_key,
        )

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
                result = client.get_result(job_id)
            markdown = result.markdown
            total = len(markdown)
            end = min(offset + limit, total)
            return {
                "job_id": job_id,
                "kind": result.kind,
                "language": result.language,
                "markdown": markdown[offset:end],
                "offset": offset,
                "next_offset": end if end < total else None,
                "total_chars": total,
                "page_count": len(result.pages),
                "warnings": list(result.warnings),
                "provenance": result.provenance.model_dump(mode="json"),
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
    ) -> dict[str, Any]:
        """Upload a small file (≤ 8 MiB decoded); returns {upload_id, sha256, size} for one job."""
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
        name = Path(filename).name or "upload"
        client = client_for(ctx)

        def call() -> dict[str, Any]:
            with client:
                up = client.upload_bytes(name, data)
            return {"upload_id": up.upload_id, "sha256": up.sha256, "size": up.size}

        return await _run(call)

    return server


__all__ = ["InputPolicy", "build_server", "TOOL_NAMES", "MAX_UPLOAD_CONTENT_BYTES",
           "MAX_RESULT_CHUNK_CHARS"]
