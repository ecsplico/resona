import json

import httpx
import pytest
import respx

from resona_project_worker.directus import DirectusClient

TOKEN = "svc-token"


@respx.mock
@pytest.mark.anyio
async def test_list_by_status_filters_and_authenticates(base_url, recording):
    route = respx.get(f"{base_url}/items/recordings").mock(
        return_value=httpx.Response(200, json={"data": [recording]})
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    result = await client.list_by_status("recordings", "pending", limit=10)
    await client.aclose()

    assert result == [recording]
    req = route.calls.last.request
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"
    assert req.url.params["filter[status][_eq]"] == "pending"
    assert req.url.params["limit"] == "10"
    assert req.url.params["sort"] == "date_created"
    assert "filter[hub_job_id][_nnull]" not in req.url.params
    assert "filter[hub_job_id][_null]" not in req.url.params


@respx.mock
@pytest.mark.anyio
async def test_list_by_status_job_filters(base_url):
    route = respx.get(f"{base_url}/items/documents").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    await client.list_by_status("documents", "processing", limit=5, with_job=True)
    await client.list_by_status("documents", "processing", limit=5, with_job=False)
    await client.aclose()
    assert route.calls[0].request.url.params["filter[hub_job_id][_nnull]"] == "true"
    assert route.calls[1].request.url.params["filter[hub_job_id][_null]"] == "true"


@respx.mock
@pytest.mark.anyio
async def test_claim_patches_active_status_and_clears_job(base_url):
    route = respx.patch(f"{base_url}/items/documents/doc-1").mock(
        return_value=httpx.Response(200, json={"data": {"id": "doc-1", "status": "processing"}})
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    ok = await client.claim("documents", "doc-1", "processing", "documents:doc-1:k")
    await client.aclose()
    assert ok is True
    assert json.loads(route.calls.last.request.content) == {
        "status": "processing", "hub_job_id": None, "hub_job_key": "documents:doc-1:k",
    }


@respx.mock
@pytest.mark.anyio
async def test_set_job(base_url):
    route = respx.patch(f"{base_url}/items/recordings/rec-1").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    await client.set_job("recordings", "rec-1", 42)
    await client.aclose()
    assert json.loads(route.calls.last.request.content) == {
        "hub_job_id": 42, "error_message": None,
    }


@respx.mock
@pytest.mark.anyio
async def test_download_file_writes_temp_file(base_url, tmp_path):
    respx.get(f"{base_url}/assets/file-1").mock(
        return_value=httpx.Response(200, content=b"RIFFfake-wav-bytes")
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    path = await client.download_file("file-1", dest_dir=tmp_path)
    await client.aclose()
    assert path.exists()
    assert path.read_bytes() == b"RIFFfake-wav-bytes"


@respx.mock
@pytest.mark.anyio
async def test_write_transcript_posts_when_none_exists(base_url):
    respx.get(f"{base_url}/items/transcripts").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    post = respx.post(f"{base_url}/items/transcripts").mock(
        return_value=httpx.Response(200, json={"data": {"id": "t-1"}})
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    await client.write_transcript(
        recording_id="rec-1", text="hallo", language="de",
        segments=[{"start": 0, "end": 1}], structured=None, engine="faster-whisper",
        provenance={"worker_id": "wrk_1"},
    )
    await client.aclose()
    body = json.loads(post.calls.last.request.content)
    assert body["recording"] == "rec-1"
    assert body["text"] == "hallo"
    assert body["engine"] == "faster-whisper"
    assert body["provenance"] == {"worker_id": "wrk_1"}


@respx.mock
@pytest.mark.anyio
async def test_write_transcript_patches_when_exists(base_url):
    respx.get(f"{base_url}/items/transcripts").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "t-existing"}]})
    )
    patch = respx.patch(f"{base_url}/items/transcripts/t-existing").mock(
        return_value=httpx.Response(200, json={"data": {"id": "t-existing"}})
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    await client.write_transcript(
        recording_id="rec-1", text="neu", language="de",
        segments=None, structured=None, engine="faster-whisper", provenance=None,
    )
    await client.aclose()
    body = json.loads(patch.calls.last.request.content)
    assert body["text"] == "neu"
    assert "recording" not in body


@respx.mock
@pytest.mark.anyio
async def test_write_document(base_url):
    route = respx.patch(f"{base_url}/items/documents/doc-1").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    await client.write_document("doc-1", markdown="# A", pages=[{"n": 1}], provenance={"worker_id": "wrk_1"})
    await client.aclose()
    assert json.loads(route.calls.last.request.content) == {
        "markdown": "# A", "pages": [{"n": 1}], "provenance": {"worker_id": "wrk_1"},
    }


@respx.mock
@pytest.mark.anyio
async def test_mark_done_and_error(base_url):
    route = respx.patch(f"{base_url}/items/recordings/rec-1").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    await client.mark_done("recordings", "rec-1")
    await client.mark_error("recordings", "rec-1", "boom")
    await client.aclose()
    assert json.loads(route.calls[0].request.content) == {
        "status": "done", "hub_job_id": None, "hub_job_key": None,
    }
    assert json.loads(route.calls[1].request.content) == {
        "status": "error", "error_message": "boom", "hub_job_id": None, "hub_job_key": None,
    }


@respx.mock
@pytest.mark.anyio
async def test_reclaim_stale_only_rows_without_job(base_url):
    get = respx.get(f"{base_url}/items/recordings").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "rec-old"}]})
    )
    patch = respx.patch(f"{base_url}/items/recordings/rec-old").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    n = await client.reclaim_stale("recordings", "transcribing", older_than_minutes=15)
    await client.aclose()
    assert n == 1
    params = get.calls.last.request.url.params
    assert params["filter[status][_eq]"] == "transcribing"
    assert params["filter[hub_job_id][_null]"] == "true"
    assert params["filter[date_updated][_lt]"] == "$NOW(-15 minutes)"
    assert json.loads(patch.calls.last.request.content) == {"status": "pending"}
