import json

import httpx
import pytest
import respx

from resona_project_worker.directus import DirectusClient
from resona_project_worker.hub import HubClient
from resona_project_worker.worker import run_once

HUB = "http://hub.test"


def mock_lists(*, pending=None, active=None, stale=None):
    """Directus list endpoints for both collections, keyed by collection name."""
    pending, active, stale = pending or {}, active or {}, stale or {}

    def handler(request, collection):
        params = request.url.params
        if "filter[date_updated][_lt]" in params:
            data = stale.get(collection, [])
        elif params["filter[status][_eq]"] == "pending":
            data = pending.get(collection, [])
        else:
            data = active.get(collection, [])
        return httpx.Response(200, json={"data": data})

    return respx.get(
        host="directus.test", path__regex=r"^/items/(?P<collection>recordings|documents)$",
    ).mock(side_effect=handler)


def mock_patch(base_url, collection, item_id):
    """PATCH that echoes the payload back (so `claim` sees its status)."""
    return respx.patch(f"{base_url}/items/{collection}/{item_id}").mock(
        side_effect=lambda request: httpx.Response(
            200, json={"data": json.loads(request.content)},
        )
    )


def bodies(route):
    return [json.loads(call.request.content) for call in route.calls]


async def cycle(base_url, tmp_path, *, engine=""):
    directus = DirectusClient(base_url, "svc")
    hub = HubClient(HUB, "hub-tok")
    try:
        return await run_once(
            directus, hub, tmp_dir=tmp_path, batch=5, stale_minutes=15, engine=engine,
        )
    finally:
        await directus.aclose()
        await hub.aclose()


@respx.mock
@pytest.mark.anyio
async def test_pending_audio_is_uploaded_and_submitted(base_url, tmp_path, recording):
    mock_lists(pending={"recordings": [recording]})
    respx.get(f"{base_url}/assets/file-1").mock(return_value=httpx.Response(200, content=b"RIFF"))
    upload = respx.post(f"{HUB}/v1/uploads").mock(
        return_value=httpx.Response(201, json={"upload_id": "up-1", "sha256": "x", "size": 4})
    )
    job = respx.post(f"{HUB}/v1/jobs").mock(
        return_value=httpx.Response(202, json={"job_id": 7, "status": "pending"})
    )
    patch = mock_patch(base_url, "recordings", "rec-1")

    assert await cycle(base_url, tmp_path) == 1

    assert upload.called
    req = job.calls.last.request
    assert req.headers["Idempotency-Key"].startswith("recordings:rec-1:")
    assert json.loads(req.content) == {
        "kind": "audio", "upload_id": "up-1", "private": True,
        "language": "de", "profile": "default",
    }
    assert bodies(patch) == [
        {"status": "transcribing"},
        {"hub_job_id": 7, "error_message": None},
    ]
    assert list(tmp_path.iterdir()) == []


@respx.mock
@pytest.mark.anyio
async def test_engine_is_sent_when_configured(base_url, tmp_path, recording):
    mock_lists(pending={"recordings": [recording]})
    respx.get(f"{base_url}/assets/file-1").mock(return_value=httpx.Response(200, content=b"RIFF"))
    respx.post(f"{HUB}/v1/uploads").mock(
        return_value=httpx.Response(201, json={"upload_id": "up-1", "sha256": "x", "size": 4})
    )
    job = respx.post(f"{HUB}/v1/jobs").mock(
        return_value=httpx.Response(202, json={"job_id": 7, "status": "pending"})
    )
    mock_patch(base_url, "recordings", "rec-1")

    await cycle(base_url, tmp_path, engine="whisper")

    assert json.loads(job.calls.last.request.content)["engine"] == "whisper"


@respx.mock
@pytest.mark.anyio
async def test_completed_job_writes_transcript_and_deletes(base_url, tmp_path, recording):
    row = {**recording, "status": "transcribing", "hub_job_id": 7}
    mock_lists(active={"recordings": [row]})
    respx.get(f"{HUB}/v1/jobs/7").mock(return_value=httpx.Response(200, json={
        "status": "completed", "error": None,
        "candidate": {"engine": "faster-whisper", "model": "large-v3"},
    }))
    respx.get(f"{HUB}/v1/jobs/7/result").mock(return_value=httpx.Response(200, json={
        "markdown": "Hallo Welt", "raw_text": "hallo welt", "language": "de",
        "segments": [{"start": 0, "end": 1, "text": "Hallo Welt"}],
        "pages": [], "structured": {"k": 1},
    }))
    delete = respx.delete(f"{HUB}/v1/jobs/7").mock(return_value=httpx.Response(204))
    respx.get(f"{base_url}/items/transcripts").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    transcript = respx.post(f"{base_url}/items/transcripts").mock(
        return_value=httpx.Response(200, json={"data": {"id": "t-1"}})
    )
    patch = mock_patch(base_url, "recordings", "rec-1")

    assert await cycle(base_url, tmp_path) == 1

    assert json.loads(transcript.calls.last.request.content) == {
        "recording": "rec-1", "text": "Hallo Welt",
        "segments": [{"start": 0, "end": 1, "text": "Hallo Welt"}],
        "structured": {"k": 1}, "engine": "faster-whisper",
    }
    assert bodies(patch) == [{"status": "done", "hub_job_id": None}]
    assert delete.called


@respx.mock
@pytest.mark.anyio
async def test_failed_job_marks_error_with_message(base_url, tmp_path, recording):
    row = {**recording, "status": "transcribing", "hub_job_id": 7}
    mock_lists(active={"recordings": [row]})
    respx.get(f"{HUB}/v1/jobs/7").mock(return_value=httpx.Response(200, json={
        "status": "failed", "error": {"code": "engine_error", "message": "kaputt"},
        "candidate": None,
    }))
    patch = mock_patch(base_url, "recordings", "rec-1")

    assert await cycle(base_url, tmp_path) == 1

    assert bodies(patch) == [
        {"status": "error", "error_message": "Verarbeitung fehlgeschlagen: kaputt"},
    ]


@respx.mock
@pytest.mark.anyio
async def test_missing_job_marks_error(base_url, tmp_path, recording):
    row = {**recording, "status": "transcribing", "hub_job_id": 7}
    mock_lists(active={"recordings": [row]})
    respx.get(f"{HUB}/v1/jobs/7").mock(
        return_value=httpx.Response(404, json={"error": "not_found", "message": "gone"})
    )
    patch = mock_patch(base_url, "recordings", "rec-1")

    await cycle(base_url, tmp_path)

    assert bodies(patch) == [{"status": "error", "error_message": "Hub-Job nicht gefunden"}]


@respx.mock
@pytest.mark.anyio
async def test_expired_result_marks_error(base_url, tmp_path, recording):
    row = {**recording, "status": "transcribing", "hub_job_id": 7}
    mock_lists(active={"recordings": [row]})
    respx.get(f"{HUB}/v1/jobs/7").mock(
        return_value=httpx.Response(200, json={"status": "completed", "error": None})
    )
    respx.get(f"{HUB}/v1/jobs/7/result").mock(return_value=httpx.Response(410))
    patch = mock_patch(base_url, "recordings", "rec-1")

    await cycle(base_url, tmp_path)

    assert bodies(patch) == [
        {"status": "error", "error_message": "Ergebnis auf dem Hub abgelaufen"},
    ]


@respx.mock
@pytest.mark.anyio
async def test_pending_job_is_left_alone(base_url, tmp_path, recording):
    row = {**recording, "status": "transcribing", "hub_job_id": 7}
    mock_lists(active={"recordings": [row]})
    respx.get(f"{HUB}/v1/jobs/7").mock(
        return_value=httpx.Response(200, json={"status": "processing", "error": None})
    )
    patch = mock_patch(base_url, "recordings", "rec-1")

    assert await cycle(base_url, tmp_path) == 0
    assert not patch.called


@respx.mock
@pytest.mark.anyio
async def test_connect_error_during_poll_leaves_row_untouched(base_url, tmp_path, recording):
    row = {**recording, "status": "transcribing", "hub_job_id": 7}
    mock_lists(active={"recordings": [row]})
    respx.get(f"{HUB}/v1/jobs/7").mock(side_effect=httpx.ConnectError("refused"))
    patch = mock_patch(base_url, "recordings", "rec-1")

    assert await cycle(base_url, tmp_path) == 0
    assert not patch.called


@respx.mock
@pytest.mark.anyio
async def test_one_failing_row_does_not_stop_others(base_url, tmp_path, recording, document):
    rec = {**recording, "status": "transcribing", "hub_job_id": 7}
    doc = {**document, "status": "processing", "hub_job_id": 9}
    mock_lists(active={"recordings": [rec], "documents": [doc]})
    respx.get(f"{HUB}/v1/jobs/7").mock(return_value=httpx.Response(500))
    respx.get(f"{HUB}/v1/jobs/9").mock(
        return_value=httpx.Response(200, json={"status": "cancelled", "error": None})
    )
    rec_patch = mock_patch(base_url, "recordings", "rec-1")
    doc_patch = mock_patch(base_url, "documents", "doc-1")

    assert await cycle(base_url, tmp_path) == 2

    assert bodies(rec_patch)[0]["status"] == "error"
    assert "HTTP 500" in bodies(rec_patch)[0]["error_message"]
    assert bodies(doc_patch) == [
        {"status": "error", "error_message": "Verarbeitung fehlgeschlagen: cancelled"},
    ]


@respx.mock
@pytest.mark.anyio
async def test_pending_document_submits_without_audio_fields(base_url, tmp_path, document):
    mock_lists(pending={"documents": [document]})
    respx.get(f"{base_url}/assets/file-2").mock(return_value=httpx.Response(200, content=b"%PDF"))
    respx.post(f"{HUB}/v1/uploads").mock(
        return_value=httpx.Response(201, json={"upload_id": "up-2", "sha256": "x", "size": 4})
    )
    job = respx.post(f"{HUB}/v1/jobs").mock(
        return_value=httpx.Response(202, json={"job_id": 9, "status": "pending"})
    )
    patch = mock_patch(base_url, "documents", "doc-1")

    assert await cycle(base_url, tmp_path, engine="whisper") == 1

    req = job.calls.last.request
    assert req.headers["Idempotency-Key"].startswith("documents:doc-1:")
    assert json.loads(req.content) == {"kind": "document", "upload_id": "up-2", "private": True}
    assert bodies(patch) == [
        {"status": "processing"},
        {"hub_job_id": 9, "error_message": None},
    ]


@respx.mock
@pytest.mark.anyio
async def test_completed_document_writes_markdown_and_pages(base_url, tmp_path, document):
    row = {**document, "status": "processing", "hub_job_id": 9}
    mock_lists(active={"documents": [row]})
    respx.get(f"{HUB}/v1/jobs/9").mock(
        return_value=httpx.Response(200, json={"status": "completed", "error": None})
    )
    respx.get(f"{HUB}/v1/jobs/9/result").mock(return_value=httpx.Response(200, json={
        "markdown": "# Arztbrief", "pages": [{"page": 1, "markdown": "# Arztbrief"}],
        "segments": [],
    }))
    delete = respx.delete(f"{HUB}/v1/jobs/9").mock(return_value=httpx.Response(204))
    patch = mock_patch(base_url, "documents", "doc-1")

    assert await cycle(base_url, tmp_path) == 1

    assert bodies(patch) == [
        {"markdown": "# Arztbrief", "pages": [{"page": 1, "markdown": "# Arztbrief"}]},
        {"status": "done", "hub_job_id": None},
    ]
    assert delete.called


@respx.mock
@pytest.mark.anyio
async def test_delete_failure_still_marks_done(base_url, tmp_path, document):
    row = {**document, "status": "processing", "hub_job_id": 9}
    mock_lists(active={"documents": [row]})
    respx.get(f"{HUB}/v1/jobs/9").mock(
        return_value=httpx.Response(200, json={"status": "completed", "error": None})
    )
    respx.get(f"{HUB}/v1/jobs/9/result").mock(
        return_value=httpx.Response(200, json={"markdown": "x", "pages": []})
    )
    respx.delete(f"{HUB}/v1/jobs/9").mock(return_value=httpx.Response(500))
    patch = mock_patch(base_url, "documents", "doc-1")

    await cycle(base_url, tmp_path)

    assert bodies(patch)[-1] == {"status": "done", "hub_job_id": None}


@respx.mock
@pytest.mark.anyio
async def test_stale_active_row_without_job_goes_back_to_pending(base_url, tmp_path):
    lists = mock_lists(stale={"recordings": [{"id": "rec-old"}]})
    patch = mock_patch(base_url, "recordings", "rec-old")

    assert await cycle(base_url, tmp_path) == 1

    assert bodies(patch) == [{"status": "pending"}]
    stale_req = lists.calls[0].request
    assert stale_req.url.params["filter[hub_job_id][_null]"] == "true"
