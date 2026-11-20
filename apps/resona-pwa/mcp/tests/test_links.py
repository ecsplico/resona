"""Upload and audio links without Directus: signing, single use, expiry, size and type
checks before anything is stored, the disabled mode and token-free logs."""
import io
import logging
import uuid
import wave

import anyio
import httpx
import pytest
from mcp import Client

from resona_dictations_mcp.directus import NotFound
from resona_dictations_mcp.http import create_app
from resona_dictations_mcp.links import (
    LinkConfigError,
    Links,
    LinkSigner,
    RedactLinkTokens,
    directus_filename,
    sniff_audio,
)
from resona_dictations_mcp.server import Caller, build_server

from .conftest import tool_error, tool_payload

SECRET = "s" * 40
PUBLIC = "https://app.example"
RID = "11111111-2222-4333-8444-555555555555"


def wav_bytes(seconds: float = 0.1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"\0\0" * int(16000 * seconds))
    return buf.getvalue()


class FakeDirectus:
    """Records what would reach Directus; ``recordings`` maps id → audio file id (None: no audio)."""

    def __init__(self, recordings=None, asset=b""):
        self.recordings = recordings or {}
        self.asset = asset
        self.uploads = []
        self.created = []

    async def upload_file(self, body, *, length, filename, content_type):
        data = b"".join([chunk async for chunk in body])
        self.uploads.append({"data": data, "length": length, "filename": filename, "type": content_type})
        return "file-1"

    async def create(self, collection, data, *, fields):
        self.created.append((collection, data))
        return {"id": data.get("id", "row-1"), **data}

    async def item(self, collection, item_id, *, fields):
        if item_id not in self.recordings:
            raise NotFound()
        return {"id": item_id, "audio_file": self.recordings[item_id]}

    async def open_asset(self, file_id, *, byte_range=None):
        return httpx.Response(200, headers={"content-type": "audio/wav", "content-length": str(len(self.asset))},
                              stream=httpx.ByteStream(self.asset))

    @property
    def stored(self):
        return bool(self.uploads or self.created)


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


def make_links(clock=None, **kwargs) -> Links:
    return Links(secret=SECRET, public_url=PUBLIC, clock=clock or Clock(), **kwargs)


def send(app, method: str, url: str, **kwargs) -> httpx.Response:
    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, url.removeprefix(PUBLIC), **kwargs)

    return anyio.run(go)


def upload_link(links: Links, fake: FakeDirectus, **kwargs) -> str:
    args = {"user_id": "user-a", "directus": fake, "recording_id": RID, "filename": "Visite 3.wav",
            "title": "Aufnahme 01.01.2027 10:00", "language": "de", "profile": None, **kwargs}
    return links.upload_link(**args)[0]


def call_tools(server, *calls):
    async def go():
        async with Client(server) as client:
            return [await client.call_tool(name, args) for name, args in calls]

    return anyio.run(go)


def test_signer_refuses_forged_altered_and_foreign_tokens():
    signer = LinkSigner(SECRET)
    token = signer.sign("upload", "a" * 32, 1_800_000_600)
    assert signer.verify("upload", token) == ("a" * 32, 1_800_000_600)
    kind, link_id, expires, mac = token.split(".")
    assert signer.verify("audio", token) is None
    assert signer.verify("audio", f"audio.{link_id}.{expires}.{mac}") is None
    assert signer.verify("upload", f"{kind}.{link_id}.{int(expires) + 3600}.{mac}") is None
    assert signer.verify("upload", f"{kind}.{'b' * 32}.{expires}.{mac}") is None
    assert LinkSigner("t" * 40).verify("upload", token) is None
    assert signer.verify("upload", "garbage") is None


def test_link_routes_refuse_to_start_without_a_usable_secret_or_public_url():
    with pytest.raises(LinkConfigError):
        create_app(directus_url="http://directus.test", link_secret="too-short", public_url=PUBLIC)
    with pytest.raises(LinkConfigError):
        create_app(directus_url="http://directus.test", link_secret=SECRET, public_url="")


def test_without_a_secret_link_routes_are_404_and_link_tools_refuse():
    app = create_app(directus_url="http://directus.test", allowed_hosts=["test"], link_secret="")
    token = LinkSigner(SECRET).sign("audio", "a" * 32, 4_000_000_000)
    resp = send(app, "GET", f"/mcp/links/audio/{token}")
    assert resp.status_code == 404 and resp.json()["error"] == "links_disabled"

    fake = FakeDirectus({RID: "file-1"})
    upload, audio = call_tools(build_server(lambda ctx: Caller(user_id="u", directus=fake), None),
                               ("create_audio_upload", {"filename": "a.wav"}), ("get_audio_link", {"id": RID}))
    assert "not enabled" in tool_error(upload) and "not enabled" in tool_error(audio)


def test_an_upload_link_works_once():
    fake = FakeDirectus()
    links = make_links()
    (issued,) = call_tools(build_server(lambda ctx: Caller(user_id="user-a", directus=fake), links),
                           ("create_audio_upload", {"filename": "Visite Müller.m4a", "language": "de"}))
    issued = tool_payload(issued)
    assert issued["upload_url"].startswith(f"{PUBLIC}/mcp/links/upload/")
    assert issued["curl"].startswith("curl --fail-with-body -T 'Visite Müller.m4a' ")
    assert issued["single_use"] is True and issued["max_bytes"] == links.max_upload_bytes
    uuid.UUID(issued["id"])
    assert not fake.stored  # issuing a link stores nothing

    audio = wav_bytes()
    first = send(links, "PUT", issued["upload_url"], content=audio)
    assert first.status_code == 201
    assert first.json() == {"id": issued["id"], "status": "pending"}
    assert fake.uploads == [{"data": audio, "length": len(audio), "filename": "Visite Muller.wav", "type": "audio/wav"}]
    collection, row = fake.created[0]
    assert collection == "recordings"
    assert row["id"] == issued["id"] and row["audio_file"] == "file-1"
    assert (row["status"], row["source"], row["title_auto"], row["language"]) == ("pending", "batch", True, "de")
    assert row["title"].startswith("Aufnahme ")

    second = send(links, "PUT", issued["upload_url"], content=audio)
    assert second.status_code == 410 and second.json()["error"] == "link_used"
    assert len(fake.uploads) == 1 and len(fake.created) == 1


def test_an_upload_link_expires():
    clock, fake = Clock(), FakeDirectus()
    links = make_links(clock)
    url = upload_link(links, fake)
    clock.now += 601
    resp = send(links, "PUT", url, content=wav_bytes())
    assert resp.status_code == 410 and resp.json()["error"] == "link_expired"
    assert not fake.stored


def test_a_tampered_upload_link_is_refused():
    fake = FakeDirectus()
    links = make_links()
    url = upload_link(links, fake)
    resp = send(links, "PUT", url[:-2] + ("AA" if not url.endswith("AA") else "BB"), content=wav_bytes())
    assert resp.status_code == 404
    assert send(links, "PUT", url, content=wav_bytes()).status_code == 201  # the real one was not spent


@pytest.mark.parametrize("body, status", [
    (wav_bytes(1.0), 413),  # 32 KB > the 1000-byte cap below
    (b"%PDF-1.7\n" + b"\0" * 200, 415),
    (b"\x89PNG\r\n\x1a\n" + b"\0" * 200, 415),
    (b"just some text, no audio", 415),
    (b"", 422),
    (None, 411),  # chunked: no Content-Length
])
def test_oversized_or_non_audio_uploads_are_refused_before_anything_is_stored(body, status):
    fake = FakeDirectus()
    links = make_links(max_upload_bytes=1000)
    url = upload_link(links, fake)

    async def chunks():
        yield wav_bytes()[:500]

    resp = send(links, "PUT", url, content=chunks() if body is None else body)
    assert resp.status_code == status
    assert not fake.stored
    # The refused attempt spent the link.
    assert send(links, "PUT", url, content=wav_bytes(0.01)).status_code == 410


def test_post_works_like_put_and_other_methods_are_refused():
    fake = FakeDirectus()
    links = make_links()
    assert send(links, "GET", upload_link(links, fake)).status_code == 405
    assert send(links, "POST", upload_link(links, fake), content=wav_bytes()).status_code == 201


def test_an_audio_link_serves_the_audio_until_it_expires():
    clock = Clock()
    audio = wav_bytes()
    fake = FakeDirectus({RID: "file-9", "22222222-2222-4333-8444-555555555555": None}, asset=audio)
    links = make_links(clock)
    server = build_server(lambda ctx: Caller(user_id="user-a", directus=fake), links)
    issued, foreign, silent = call_tools(
        server,
        ("get_audio_link", {"id": RID}),
        ("get_audio_link", {"id": str(uuid.uuid4())}),
        ("get_audio_link", {"id": "22222222-2222-4333-8444-555555555555"}),
    )
    url = tool_payload(issued)["audio_url"]
    assert url.startswith(f"{PUBLIC}/mcp/links/audio/")
    assert "not found" in tool_error(foreign)
    assert "no audio" in tool_error(silent)

    for _ in range(2):  # players fetch more than once while the link is valid
        resp = send(links, "GET", url)
        assert resp.status_code == 200 and resp.content == audio
        assert resp.headers["content-type"] == "audio/wav"
        assert resp.headers["cache-control"] == "private, no-store"
    assert send(links, "PUT", url, content=b"x").status_code == 405

    clock.now += 601
    resp = send(links, "GET", url)
    assert resp.status_code == 410 and resp.json()["error"] == "link_expired"
    # An audio token is no upload token, and the reverse.
    assert send(links, "PUT", url.replace("/audio/", "/upload/"), content=audio).status_code == 404


def test_audio_link_of_a_deleted_dictation_is_404():
    fake = FakeDirectus({RID: "file-9"}, asset=b"x")
    links = make_links()
    url = links.audio_link(user_id="user-a", directus=fake, recording_id=RID)[0]
    del fake.recordings[RID]
    assert send(links, "GET", url).status_code == 404


@pytest.mark.parametrize("head, expected", [
    (wav_bytes()[:12], ("audio/wav", "wav")),
    (b"ID3\x04\x00\x00\x00\x00\x00\x00\x00\x00", ("audio/mpeg", "mp3")),
    (b"\xff\xfb\x90\x00", ("audio/mpeg", "mp3")),
    (b"\xff\xf1\x50\x80", ("audio/aac", "aac")),
    (b"OggS\x00\x02", ("audio/ogg", "ogg")),
    (b"fLaC\x00\x00", ("audio/flac", "flac")),
    (b"\x00\x00\x00\x20ftypM4A ", ("audio/mp4", "m4a")),
    (b"\x1a\x45\xdf\xa3\x01", ("audio/webm", "webm")),
    (b"%PDF-1.7", None),
    (b"PK\x03\x04", None),
    (b"", None),
])
def test_sniff_audio(head, expected):
    assert sniff_audio(head) == expected


def test_directus_filename_is_safe():
    assert directus_filename("../../etc/passwd", "wav") == "passwd.wav"
    assert directus_filename('Visite "Ä"\r\n.mp3', "mp3") == "Visite _A.mp3"
    assert directus_filename("C:\\Users\\x\\note.m4a", "wav") == "note.wav"
    assert directus_filename("...", "ogg") == "audio.ogg"


def test_links_log_no_token():
    links = make_links()
    url = upload_link(links, FakeDirectus())
    token = url.rsplit("/", 1)[1]
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                               ("127.0.0.1:1", "PUT", f"/mcp/links/upload/{token}", "1.1", 201), None)
    RedactLinkTokens().filter(record)
    assert token not in record.getMessage()
    assert "/mcp/links/upload/" in record.getMessage()
