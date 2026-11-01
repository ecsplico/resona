"""Tool surface behaviour, driven through an in-process MCP client."""
import base64
import os
from pathlib import Path

import anyio
import pytest
from mcp import Client
from resona_client.client import AudioOptions, ResonaAPIError
from resona_job_core import JobResult, Provenance

from resona_mcp.server import (
    MAX_RESULT_CHUNK_CHARS,
    MAX_UPLOAD_CONTENT_BYTES,
    TOOL_NAMES,
    InputPolicy,
    build_server,
)

FORBIDDEN = {"put_settings", "set_settings", "put_profile", "delete_profile", "delete_job",
             "issue_credential", "revoke_credential", "enroll_worker", "approve_worker"}


def call(server, name: str, args: dict | None = None):
    async def go():
        async with Client(server) as client:
            return await client.call_tool(name, args or {})
    return anyio.run(go)


def tool_names(server) -> set[str]:
    async def go():
        async with Client(server) as client:
            return {t.name for t in (await client.list_tools()).tools}
    return anyio.run(go)


def local(hub, root: Path | None):
    return build_server(lambda ctx: hub.client(), InputPolicy(allow_paths=True, root=root))


def remote(hub):
    return build_server(lambda ctx: hub.client(), InputPolicy(allow_paths=False))


def test_exposes_exactly_the_read_and_submit_tools(hub):
    names = tool_names(remote(hub))
    assert names == set(TOOL_NAMES)
    assert not names & FORBIDDEN


# ── upload_content ─────────────────────────────────────────────────────────

def test_upload_content_accepts_the_8_mib_maximum(hub, payload):
    data = b"\0" * MAX_UPLOAD_CONTENT_BYTES
    out = payload(call(remote(hub), "upload_content",
                       {"filename": "../../memo.wav", "content_base64": base64.b64encode(data).decode()}))
    assert out["size"] == MAX_UPLOAD_CONTENT_BYTES
    assert hub.calls == [("upload_bytes", ("memo.wav", MAX_UPLOAD_CONTENT_BYTES))]


def test_upload_content_rejects_oversize_before_uploading(hub, error_text):
    data = b"\0" * (MAX_UPLOAD_CONTENT_BYTES + 1)
    msg = error_text(call(remote(hub), "upload_content",
                          {"filename": "big.wav", "content_base64": base64.b64encode(data).decode()}))
    assert "8 MiB" in msg
    assert hub.calls == []


def test_upload_content_rejects_invalid_base64(hub, error_text):
    msg = error_text(call(remote(hub), "upload_content", {"filename": "a.wav", "content_base64": "not base64!"}))
    assert "base64" in msg
    assert hub.calls == []


# ── submission sources ─────────────────────────────────────────────────────

def test_submit_with_upload_id_returns_job_immediately(hub, payload):
    out = payload(call(remote(hub), "transcribe_audio", {
        "upload_id": "upl_" + "c" * 24, "execution_profile": "cpu-private",
        "language": "de", "options": {"word_timestamps": True},
    }))
    assert out == {"job_id": 7, "status": "pending", "private": True}
    name, (upload_id, kwargs) = hub.calls[0]
    assert (name, upload_id) == ("submit_upload", "upl_" + "c" * 24)
    assert kwargs["kind"] == "audio" and kwargs["private"] is True
    assert kwargs["execution_profile"] == "cpu-private" and kwargs["language"] == "de"
    assert kwargs["options"] == AudioOptions(word_timestamps=True)


def test_convert_document_rejects_audio_only_options(hub, error_text):
    error_text(call(remote(hub), "convert_document", {"upload_id": "upl_" + "c" * 24, "options": {"translate": True}}))
    assert hub.calls == []


@pytest.mark.parametrize("args", [
    {},
    {"upload_id": "upl_" + "c" * 24, "path": "a.wav"},
    {"upload_id": "https://example.com/a.wav"},
    {"upload_id": "../../etc/passwd"},
])
def test_submit_requires_exactly_one_valid_source(hub, error_text, args):
    error_text(call(remote(hub), "transcribe_audio", args))
    assert hub.calls == []


@pytest.mark.parametrize("path", ["/etc/hosts", "audio/a.wav", "https://example.com/a.wav", "file:///etc/hosts"])
def test_remote_server_rejects_paths_and_urls(hub, error_text, path):
    msg = error_text(call(remote(hub), "convert_document", {"path": path}))
    assert "upload_content" in msg
    assert hub.calls == []


def test_missing_root_disables_path_input(hub, error_text, tmp_path):
    (tmp_path / "a.wav").write_bytes(b"RIFF")
    msg = error_text(call(local(hub, None), "transcribe_audio", {"path": str(tmp_path / "a.wav")}))
    assert "RESONA_MCP_ROOT" in msg
    assert hub.calls == []


def test_path_inside_root_is_uploaded_by_real_path(hub, payload, tmp_path):
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    target = root / "sub" / "a.wav"
    target.write_bytes(b"RIFF")
    out = payload(call(local(hub, root), "transcribe_audio", {"path": "sub/a.wav"}))
    assert out["job_id"] == 7
    assert hub.calls[0] == ("upload", target.resolve())
    assert hub.calls[1][1][0] == "upl_" + "a" * 24


def test_path_escapes_are_rejected(hub, error_text, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.wav"
    outside.write_bytes(b"RIFF")
    os.symlink(outside, root / "link.wav")
    os.symlink(tmp_path, root / "dirlink")
    (root / "folder").mkdir()
    server = local(hub, root)

    for path in ["../secret.wav", str(outside), "link.wav", "dirlink/secret.wav",
                 "folder", "missing.wav", "file:///etc/hosts"]:
        error_text(call(server, "transcribe_audio", {"path": path}))
    assert hub.calls == []


def test_symlink_staying_inside_root_is_allowed(hub, payload, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "real.wav").write_bytes(b"RIFF")
    os.symlink(root / "real.wav", root / "alias.wav")
    payload(call(local(hub, root), "transcribe_audio", {"path": "alias.wav"}))
    assert hub.calls[0] == ("upload", (root / "real.wav").resolve())


# ── results ────────────────────────────────────────────────────────────────

def test_result_chunks_reconstruct_markdown_exactly(hub, payload):
    markdown = "# Befund\n\nÄrztin übernimmt 🩺 – Zeile\r\n" * 40 + "Ende ohne Umbruch"
    hub.result = JobResult(kind="document", raw_text="raw", markdown=markdown, provenance=Provenance())
    server = remote(hub)

    chunks, offset, totals = [], 0, set()
    while offset is not None:
        out = payload(call(server, "get_result", {"job_id": 5, "offset": offset, "limit": 97}))
        assert len(out["markdown"]) <= 97
        chunks.append(out["markdown"])
        totals.add(out["total_chars"])
        offset = out["next_offset"]
    assert "".join(chunks) == markdown
    assert totals == {len(markdown)}
    assert len(chunks) == -(-len(markdown) // 97)
    # Every chunk is a hub range read; the full JobResult is never fetched.
    assert {name for name, _ in hub.calls} == {"get_result_markdown"}
    assert hub.calls[1][1] == (5, 97, 97)


def test_result_default_limit_is_the_maximum_chunk(hub, payload):
    payload(call(remote(hub), "get_result", {"job_id": 5}))
    assert hub.calls == [("get_result_markdown", (5, 0, MAX_RESULT_CHUNK_CHARS))]
    assert MAX_RESULT_CHUNK_CHARS == 32_768


def test_result_limit_is_bounded(hub, error_text):
    error_text(call(remote(hub), "get_result", {"job_id": 5, "limit": MAX_RESULT_CHUNK_CHARS + 1}))
    assert hub.calls == []


def test_hub_errors_become_tool_errors(hub, error_text):
    hub.error = ResonaAPIError(404, "not_found", "Job not found")
    msg = error_text(call(remote(hub), "get_job", {"job_id": 99}))
    assert "404 not_found" in msg


def test_read_tools_return_hub_state(hub, payload):
    server = remote(hub)
    assert payload(call(server, "get_job", {"job_id": 3}))["status"] == "completed"
    assert payload(call(server, "cancel_job", {"job_id": 3}))["status"] == "cancelled"
    assert payload(call(server, "get_settings"))["organization_id"] == "org-a"
    engines = payload(call(server, "list_engines"))
    assert engines["default_execution_profile"] == "private-auto"
    assert "cpu-private" in engines["execution_profiles"]
