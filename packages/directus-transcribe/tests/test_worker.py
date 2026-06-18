import json

import httpx
import pytest
import respx

from resona_directus_transcribe.client import DirectusClient
from resona_directus_transcribe.transcribe import TranscribeClient
from resona_directus_transcribe.worker import process_one, run_once

D = "http://directus.test"
A = "http://api.test"


def _clients():
    return (
        DirectusClient(base_url=D, token="t"),
        TranscribeClient(base_url=A, api_key=""),
    )


@respx.mock
@pytest.mark.anyio
async def test_process_one_happy_path(tmp_path, recording):
    respx.get(f"{D}/assets/file-1").mock(return_value=httpx.Response(200, content=b"RIFF"))
    respx.post(f"{A}/v1/audio/transcriptions").mock(
        return_value=httpx.Response(200, json={"text": "hi", "language": "de", "segments": []})
    )
    tx = respx.post(f"{D}/items/transcripts").mock(return_value=httpx.Response(200, json={"data": {}}))
    patch = respx.patch(f"{D}/items/recordings/rec-1").mock(return_value=httpx.Response(200, json={"data": {}}))

    d, a = _clients()
    await process_one(recording, d, a, tmp_dir=tmp_path)
    await d.aclose(); await a.aclose()

    assert tx.called
    assert json.loads(patch.calls.last.request.content) == {"status": "done"}
    # temp audio cleaned up
    assert list(tmp_path.iterdir()) == []


@respx.mock
@pytest.mark.anyio
async def test_process_one_marks_error_on_api_failure(tmp_path, recording):
    respx.get(f"{D}/assets/file-1").mock(return_value=httpx.Response(200, content=b"RIFF"))
    respx.post(f"{A}/v1/audio/transcriptions").mock(return_value=httpx.Response(500))
    patch = respx.patch(f"{D}/items/recordings/rec-1").mock(return_value=httpx.Response(200, json={"data": {}}))

    d, a = _clients()
    await process_one(recording, d, a, tmp_dir=tmp_path)
    await d.aclose(); await a.aclose()

    body = json.loads(patch.calls.last.request.content)
    assert body["status"] == "error"
    assert "error_message" in body
    assert list(tmp_path.iterdir()) == []


@respx.mock
@pytest.mark.anyio
async def test_run_once_claims_and_processes_each(tmp_path):
    recs = [{"id": f"rec-{i}", "audio_file": f"f-{i}", "language": "de", "profile": "default"}
            for i in range(3)]
    # stale sweep (no stale) then list_pending
    respx.get(f"{D}/items/recordings").mock(side_effect=[
        httpx.Response(200, json={"data": []}),       # reclaim_stale sweep
        httpx.Response(200, json={"data": recs}),     # list_pending
    ])
    for r in recs:
        respx.patch(f"{D}/items/recordings/{r['id']}").mock(return_value=httpx.Response(200, json={"data": {"status": "transcribing"}}))
        respx.get(f"{D}/assets/{r['audio_file']}").mock(return_value=httpx.Response(200, content=b"RIFF"))
    respx.post(f"{A}/v1/audio/transcriptions").mock(return_value=httpx.Response(200, json={"text": "x", "language": "de", "segments": []}))
    tx = respx.post(f"{D}/items/transcripts").mock(return_value=httpx.Response(200, json={"data": {}}))

    d, a = _clients()
    processed = await run_once(d, a, tmp_dir=tmp_path, concurrency=2, stale_minutes=15)
    await d.aclose(); await a.aclose()

    assert processed == 3
    assert tx.call_count == 3
