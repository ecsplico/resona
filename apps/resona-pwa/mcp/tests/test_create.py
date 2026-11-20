"""Create tools and links against a real Directus (the app's compose image + bootstrap).

Fresh users per module, so the read tests' exact id sets stay untouched. The project
worker's part (a ``pending`` recording with audio → hub job → transcript) is the
worker's own path; these tests stop at the row it picks up.
"""
import io
import json
import wave

import httpx
import pytest

from resona_dictations_mcp.http import create_app

from .conftest import free_port, serve, tool_error, tool_payload
from .test_isolation import call_tool

SECRET = "link-secret-for-tests-only-0123456789abcdef"
MAX_BYTES = 64 * 1024


def wav_bytes(seconds: float) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"\0\0" * int(16000 * seconds))
    return buf.getvalue()


@pytest.fixture(scope="module")
def users(directus_admin):
    return directus_admin.user("c"), directus_admin.user("d")


@pytest.fixture(scope="module")
def linked(directus_admin):
    """The service with links on; its public URL is the served address itself."""
    port = free_port()
    app = create_app(directus_url=directus_admin.url, allowed_hosts=["127.0.0.1:*"],
                     allowed_origins=["https://app.example"], link_secret=SECRET,
                     public_url=f"http://127.0.0.1:{port}", max_upload_bytes=MAX_BYTES)
    with serve(app, port) as url:
        yield url


def files_of(admin, user) -> list[dict]:
    return admin.call("GET", "/files", params={
        "filter": json.dumps({"uploaded_by": {"_eq": user.id}}), "fields": "id,type,filename_download"})


def recordings_of(admin, user) -> list[dict]:
    return admin.call("GET", "/items/recordings", params={
        "filter": json.dumps({"user_created": {"_eq": user.id}}), "fields": "id"})


def test_a_text_dictation_appears_with_the_right_owner(linked, directus_admin, users):
    c, d = users
    created = tool_payload(call_tool(linked, c.token, "create_dictation_from_text",
                                     {"text": "Befund: unauffällig.", "language": "de"}))
    row = directus_admin.call("GET", f"/items/recordings/{created['id']}", params={
        "fields": "user_created,status,source,title,title_auto,language"})
    assert row["user_created"] == c.id
    assert (row["status"], row["source"], row["title_auto"], row["language"]) == ("done", "live", True, "de")
    assert row["title"].startswith("Diktat ") and created["title"] == row["title"]

    mine = tool_payload(call_tool(linked, c.token, "get_dictation", {"id": created["id"]}))
    assert mine["text"] == "Befund: unauffällig." and mine["status"] == "done"
    assert "not found" in tool_error(call_tool(linked, d.token, "get_dictation", {"id": created["id"]}))

    titled = tool_payload(call_tool(linked, c.token, "create_dictation_from_text",
                                    {"text": "Kurz.", "title": "Visite Station 3"}))
    assert (titled["title"], titled["title_auto"]) == ("Visite Station 3", False)


def test_an_upload_link_creates_a_pending_recording_once(linked, directus_admin, users):
    c, d = users
    issued = tool_payload(call_tool(linked, c.token, "create_audio_upload",
                                    {"filename": "visite.wav", "language": "de"}))
    audio = wav_bytes(0.5)
    first = httpx.put(issued["upload_url"], content=audio, timeout=30.0)
    assert first.status_code == 201, first.text

    row = directus_admin.call("GET", f"/items/recordings/{issued['id']}", params={
        "fields": "user_created,status,source,title_auto,language,audio_file.uploaded_by,audio_file.type"})
    assert row["user_created"] == c.id
    assert (row["status"], row["source"], row["title_auto"], row["language"]) == ("pending", "batch", True, "de")
    assert row["audio_file"] == {"uploaded_by": c.id, "type": "audio/wav"}

    second = httpx.put(issued["upload_url"], content=audio, timeout=30.0)
    assert second.status_code == 410

    link = tool_payload(call_tool(linked, c.token, "get_audio_link", {"id": issued["id"]}))
    served = httpx.get(link["audio_url"], timeout=30.0)
    assert served.status_code == 200 and served.content == audio
    part = httpx.get(link["audio_url"], headers={"Range": "bytes=0-3"}, timeout=30.0)
    assert part.status_code == 206 and part.content == b"RIFF"

    # Another user gets no link to it, and a link of theirs never reads it.
    assert "not found" in tool_error(call_tool(linked, d.token, "get_audio_link", {"id": issued["id"]}))


def test_oversized_or_non_audio_uploads_store_nothing(linked, directus_admin, users):
    c, _ = users
    files, rows = len(files_of(directus_admin, c)), len(recordings_of(directus_admin, c))
    for body, status in ((wav_bytes(5.0), 413), (b"%PDF-1.7\n" + b"x" * 500, 415)):
        issued = tool_payload(call_tool(linked, c.token, "create_audio_upload", {"filename": "x.wav"}))
        assert httpx.put(issued["upload_url"], content=body, timeout=30.0).status_code == status
    assert len(files_of(directus_admin, c)) == files
    assert len(recordings_of(directus_admin, c)) == rows


def test_a_revoked_token_spends_the_link_without_a_row(linked, directus_admin):
    e = directus_admin.user("e")
    issued = tool_payload(call_tool(linked, e.token, "create_audio_upload", {"filename": "x.wav"}))
    directus_admin.revoke(e)
    resp = httpx.put(issued["upload_url"], content=wav_bytes(0.1), timeout=30.0)
    assert resp.status_code == 401
    assert files_of(directus_admin, e) == [] and recordings_of(directus_admin, e) == []
