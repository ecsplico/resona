import json

import httpx
import pytest
import respx

from resona_directus_transcribe.client import DirectusClient

TOKEN = "svc-token"


@respx.mock
@pytest.mark.anyio
async def test_list_pending_filters_and_authenticates(base_url, recording):
    route = respx.get(f"{base_url}/items/recordings").mock(
        return_value=httpx.Response(200, json={"data": [recording]})
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    result = await client.list_pending(limit=10)
    await client.aclose()

    assert result == [recording]
    req = route.calls.last.request
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"
    assert req.url.params["filter[status][_eq]"] == "pending"
    assert req.url.params["limit"] == "10"
    assert req.url.params["sort"] == "date_created"


@respx.mock
@pytest.mark.anyio
async def test_claim_patches_status_to_transcribing(base_url):
    route = respx.patch(f"{base_url}/items/recordings/rec-1").mock(
        return_value=httpx.Response(200, json={"data": {"id": "rec-1", "status": "transcribing"}})
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    ok = await client.claim("rec-1")
    await client.aclose()

    assert ok is True
    assert json.loads(route.calls.last.request.content) == {"status": "transcribing"}


@respx.mock
@pytest.mark.anyio
async def test_download_audio_writes_temp_file(base_url, tmp_path):
    respx.get(f"{base_url}/assets/file-1").mock(
        return_value=httpx.Response(200, content=b"RIFFfake-wav-bytes")
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    path = await client.download_audio("file-1", dest_dir=tmp_path)
    await client.aclose()
    assert path.exists()
    assert path.read_bytes() == b"RIFFfake-wav-bytes"


@respx.mock
@pytest.mark.anyio
async def test_write_transcript_posts_payload(base_url):
    route = respx.post(f"{base_url}/items/transcripts").mock(
        return_value=httpx.Response(200, json={"data": {"id": "t-1"}})
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    await client.write_transcript(
        recording_id="rec-1", text="hallo", language="de",
        segments=[{"start": 0, "end": 1}], structured=None, engine="faster-whisper",
    )
    await client.aclose()
    body = json.loads(route.calls.last.request.content)
    assert body["recording"] == "rec-1"
    assert body["text"] == "hallo"
    assert body["engine"] == "faster-whisper"


@respx.mock
@pytest.mark.anyio
async def test_mark_done_and_error(base_url):
    done = respx.patch(f"{base_url}/items/recordings/rec-1").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    await client.mark_done("rec-1")
    await client.mark_error("rec-1", "boom")
    await client.aclose()
    assert json.loads(done.calls[0].request.content) == {"status": "done"}
    err = json.loads(done.calls[1].request.content)
    assert err == {"status": "error", "error_message": "boom"}


@respx.mock
@pytest.mark.anyio
async def test_reclaim_stale_resets_old_transcribing(base_url):
    respx.get(f"{base_url}/items/recordings").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "rec-old"}]})
    )
    patch = respx.patch(f"{base_url}/items/recordings/rec-old").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    n = await client.reclaim_stale(older_than_minutes=15)
    await client.aclose()
    assert n == 1
    assert json.loads(patch.calls.last.request.content) == {"status": "pending"}
