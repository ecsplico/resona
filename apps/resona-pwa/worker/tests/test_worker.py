import json
import logging

import httpx
import pytest
import respx

from resona_project_worker.directus import DirectusClient
from resona_project_worker.hub import HubClient
from resona_project_worker.worker import run_once

HUB = "http://hub.test"


def mock_lists(*, pending=None, active=None, stale=None, cleanup=None):
    """Directus list endpoints for all polled collections, keyed by collection name
    (transcripts filter on `postprocess_status`). `cleanup`: `recording_parts` rows the
    audio-cleanup query sees, filtered here like Directus would (audio and
    `offset_seconds` set, `status` in the requested list)."""
    pending, active, stale = pending or {}, active or {}, stale or {}

    def handler(request, collection):
        params = request.url.params
        status = params.get("filter[status][_eq]") or params.get("filter[postprocess_status][_eq]")
        if "filter[offset_seconds][_nnull]" in params:
            statuses = params["filter[status][_in]"].split(",")
            data = [
                {"id": r["id"], "audio": r["audio"]} for r in cleanup or []
                if r.get("audio") is not None and r.get("offset_seconds") is not None
                and r.get("status") in statuses
            ]
        elif "filter[date_updated][_lt]" in params:
            data = stale.get(collection, [])
        elif status == "pending":
            data = pending.get(collection, [])
        else:
            data = active.get(collection, [])
        return httpx.Response(200, json={"data": data})

    return respx.get(
        host="directus.test",
        path__regex=r"^/items/(?P<collection>recordings|documents|transcripts|recording_parts)$",
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


PROV = {"worker_id": "wrk_1", "stages": [{"stage": "asr", "engine": "faster-whisper", "model": "large-v3", "seconds": 2.5}]}

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
    key = req.headers["Idempotency-Key"]
    assert bodies(patch) == [
        {"status": "transcribing", "hub_job_id": None, "hub_job_key": key, "error_message": None},
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
        "provenance": PROV,
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
        "structured": {"k": 1}, "engine": "faster-whisper", "provenance": PROV,
        "raw_text": None, "applied_profile": None, "processed_text": None,
        "postprocess_status": None, "postprocess_profile": None, "postprocess_job_id": None,
        "postprocess_job_key": None, "postprocess_error": None, "postprocess_queue_reason": None,
    }
    assert bodies(patch) == [{"status": "done", "hub_job_id": None, "hub_job_key": None}]
    assert delete.called


async def _complete_recording(
    base_url, tmp_path, recording, markdown, *,
    title_applied=True, title_status=200, title_updates=None, **flags,
):
    """Completes rec-1's job. The guarded title update answers `title_status`;
    `title_applied=False` = no row matched (renamed meanwhile)."""
    row = {**recording, "status": "transcribing", "hub_job_id": 7, **flags}
    mock_lists(active={"recordings": [row]})
    respx.get(f"{HUB}/v1/jobs/7").mock(return_value=httpx.Response(200, json={
        "status": "completed", "error": None,
        "candidate": {"engine": "faster-whisper", "model": "large-v3"},
    }))
    respx.get(f"{HUB}/v1/jobs/7/result").mock(return_value=httpx.Response(200, json={
        "markdown": markdown, "raw_text": markdown, "language": "de",
        "segments": [], "pages": [], "structured": None, "provenance": PROV,
    }))
    respx.delete(f"{HUB}/v1/jobs/7").mock(return_value=httpx.Response(204))
    respx.get(f"{base_url}/items/transcripts").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    respx.post(f"{base_url}/items/transcripts").mock(
        return_value=httpx.Response(200, json={"data": {"id": "t-1"}})
    )
    patch = mock_patch(base_url, "recordings", "rec-1")
    # Guarded auto-title update: empty `data` = the row was renamed meanwhile.
    title_route = respx.patch(f"{base_url}/items/recordings").mock(
        return_value=httpx.Response(
            title_status, json={"data": [{"id": "rec-1"}] if title_applied else []},
        )
    )
    assert await cycle(base_url, tmp_path) == 1
    if title_updates is not None:
        title_updates.extend(bodies(title_route))
    return bodies(patch)


@respx.mock
@pytest.mark.anyio
async def test_auto_title_replaces_placeholder_and_clears_flag(base_url, tmp_path, recording):
    updates = []
    sent = await _complete_recording(
        base_url, tmp_path, recording,
        "Kontrolle nach Knie-OP rechts. Patient beschwerdefrei.",
        title="Aufnahme 10.11.2026 09:05", title_auto=True, title_updates=updates,
    )
    assert updates == [{
        "query": {"filter": {"id": {"_eq": "rec-1"}, "title_auto": {"_eq": True}}},
        "data": {"title": "Kontrolle nach Knie-OP rechts", "title_auto": False},
    }]
    assert all("title" not in body for body in sent)
    assert {"status": "done", "hub_job_id": None, "hub_job_key": None} in sent


@respx.mock
@pytest.mark.anyio
@pytest.mark.parametrize("flags", [{}, {"title_auto": False}, {"title_auto": None}])
async def test_title_is_kept_without_auto_flag(base_url, tmp_path, recording, flags):
    updates = []
    sent = await _complete_recording(
        base_url, tmp_path, recording, "Kontrolle nach Knie-OP rechts.",
        title_updates=updates, **flags,
    )
    assert all("title" not in body for body in sent)
    assert updates == []


@respx.mock
@pytest.mark.anyio
@pytest.mark.parametrize("markdown", ["", "äh ähm ja."])
async def test_auto_title_untouched_without_suggestion(base_url, tmp_path, recording, markdown):
    updates = []
    sent = await _complete_recording(
        base_url, tmp_path, recording, markdown,
        title="Aufnahme 10.11.2026 09:05", title_auto=True, title_updates=updates,
    )
    assert all("title" not in body and "title_auto" not in body for body in sent)
    assert updates == []


@respx.mock
@pytest.mark.anyio
async def test_renamed_row_keeps_its_title_and_job_still_completes(base_url, tmp_path, recording):
    updates = []
    sent = await _complete_recording(
        base_url, tmp_path, recording, "Kontrolle nach Knie-OP rechts.",
        title="Aufnahme 10.11.2026 09:05", title_auto=True,
        title_applied=False, title_updates=updates,
    )
    assert len(updates) == 1
    assert all("title" not in body for body in sent)
    assert {"status": "done", "hub_job_id": None, "hub_job_key": None} in sent


@respx.mock
@pytest.mark.anyio
async def test_failed_derived_title_is_logged_and_never_fails_the_row(
    base_url, tmp_path, recording, caplog,
):
    markdown = "Kontrolle nach Knie-OP rechts. Patient beschwerdefrei."
    updates = []
    with caplog.at_level(logging.WARNING, logger="resona_project_worker.worker"):
        sent = await _complete_recording(
            base_url, tmp_path, recording, markdown,
            title="Aufnahme 10.11.2026 09:05", title_auto=True,
            title_status=500, title_updates=updates,
        )
    assert len(updates) == 1
    assert sent == [{"status": "done", "hub_job_id": None, "hub_job_key": None}]
    assert "could not apply derived title to recordings rec-1" in caplog.text
    assert "Knie" not in caplog.text


@respx.mock
@pytest.mark.anyio
async def test_retranscription_resets_reprocessing_and_deletes_its_hub_job(
    base_url, tmp_path, recording,
):
    # "Neu transkribieren" while a text job (55) still runs on the old transcript.
    row = {**recording, "status": "transcribing", "hub_job_id": 7}
    respx.get(f"{HUB}/v1/jobs/7").mock(return_value=httpx.Response(200, json={
        "status": "completed", "error": None, "candidate": {"engine": "faster-whisper"},
    }))
    respx.get(f"{HUB}/v1/jobs/7/result").mock(return_value=httpx.Response(200, json={
        "markdown": "Neu", "segments": [], "structured": None, "provenance": None,
    }))
    respx.delete(f"{HUB}/v1/jobs/7").mock(return_value=httpx.Response(204))
    old_text_job = respx.delete(f"{HUB}/v1/jobs/55").mock(return_value=httpx.Response(204))

    # The transcript lookup before the write; registered before the list mocks
    # because respx takes the first matching route.
    respx.get(f"{base_url}/items/transcripts", params={"filter[recording][_eq]": "rec-1"}).mock(
        return_value=httpx.Response(200, json={"data": [{"id": "t-1", "postprocess_job_id": 55}]}),
    )
    mock_lists(active={"recordings": [row]})
    written = mock_patch(base_url, "transcripts", "t-1")
    mock_patch(base_url, "recordings", "rec-1")

    assert await cycle(base_url, tmp_path) == 1

    body = bodies(written)[0]
    assert body["text"] == "Neu"
    assert {k: v for k, v in body.items() if k.startswith("postprocess_")} == {
        "postprocess_status": None, "postprocess_profile": None, "postprocess_job_id": None,
        "postprocess_job_key": None, "postprocess_error": None, "postprocess_queue_reason": None,
    }
    assert body["raw_text"] is None and body["applied_profile"] is None
    assert old_text_job.called


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
        {
            "status": "error", "error_message": "Verarbeitung fehlgeschlagen: kaputt",
            "hub_job_id": None, "hub_job_key": None,
        },
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

    assert bodies(patch) == [
        {"status": "error", "error_message": "Hub-Job nicht gefunden", "hub_job_id": None, "hub_job_key": None},
    ]


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
        {
            "status": "error", "error_message": "Ergebnis auf dem Hub abgelaufen",
            "hub_job_id": None, "hub_job_key": None,
        },
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
@pytest.mark.parametrize("hub_reply", [
    {"side_effect": httpx.ConnectError("refused")},
    {"side_effect": httpx.ReadError("connection reset")},
    {"return_value": httpx.Response(502)},
    {"return_value": httpx.Response(503, json={"error": "unavailable"})},
], ids=["connect-error", "read-error", "http-502", "http-503"])
async def test_transient_hub_failure_during_poll_leaves_row_untouched(
    base_url, tmp_path, recording, hub_reply,
):
    row = {**recording, "status": "transcribing", "hub_job_id": 7}
    mock_lists(active={"recordings": [row]})
    respx.get(f"{HUB}/v1/jobs/7").mock(**hub_reply)
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
        {
            "status": "error", "error_message": "Verarbeitung fehlgeschlagen: cancelled",
            "hub_job_id": None, "hub_job_key": None,
        },
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
    key = req.headers["Idempotency-Key"]
    assert bodies(patch) == [
        {"status": "processing", "hub_job_id": None, "hub_job_key": key, "error_message": None},
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
        "segments": [], "provenance": PROV,
    }))
    delete = respx.delete(f"{HUB}/v1/jobs/9").mock(return_value=httpx.Response(204))
    patch = mock_patch(base_url, "documents", "doc-1")

    assert await cycle(base_url, tmp_path) == 1

    assert bodies(patch) == [
        {"markdown": "# Arztbrief", "pages": [{"page": 1, "markdown": "# Arztbrief"}],
         "provenance": PROV},
        {"status": "done", "hub_job_id": None, "hub_job_key": None},
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

    assert bodies(patch)[-1] == {"status": "done", "hub_job_id": None, "hub_job_key": None}


@respx.mock
@pytest.mark.anyio
async def test_stale_active_row_without_job_goes_back_to_pending(base_url, tmp_path):
    lists = mock_lists(stale={"recordings": [{"id": "rec-old"}]})
    patch = mock_patch(base_url, "recordings", "rec-old")

    assert await cycle(base_url, tmp_path) == 1

    assert bodies(patch) == [{"status": "pending"}]
    stale_req = lists.calls[0].request
    assert stale_req.url.params["filter[hub_job_id][_null]"] == "true"


@respx.mock
@pytest.mark.anyio
async def test_existing_job_key_is_reused_on_resubmit(base_url, tmp_path, recording):
    """A stale-reclaimed row whose first create was lost resubmits under the same key."""
    row = {**recording, "hub_job_key": "recordings:rec-1:first"}
    mock_lists(pending={"recordings": [row]})
    respx.get(f"{base_url}/assets/file-1").mock(return_value=httpx.Response(200, content=b"RIFF"))
    respx.post(f"{HUB}/v1/uploads").mock(
        return_value=httpx.Response(201, json={"upload_id": "up-1", "sha256": "x", "size": 4})
    )
    job = respx.post(f"{HUB}/v1/jobs").mock(
        return_value=httpx.Response(202, json={"job_id": 7, "status": "pending"})
    )
    patch = mock_patch(base_url, "recordings", "rec-1")

    await cycle(base_url, tmp_path)

    assert job.calls.last.request.headers["Idempotency-Key"] == "recordings:rec-1:first"
    assert bodies(patch)[0]["hub_job_key"] == "recordings:rec-1:first"


@respx.mock
@pytest.mark.anyio
async def test_job_key_is_persisted_before_create(base_url, tmp_path, recording):
    """If create_job times out, the key is already on the row for the retry."""
    mock_lists(pending={"recordings": [recording]})
    respx.get(f"{base_url}/assets/file-1").mock(return_value=httpx.Response(200, content=b"RIFF"))
    respx.post(f"{HUB}/v1/uploads").mock(
        return_value=httpx.Response(201, json={"upload_id": "up-1", "sha256": "x", "size": 4})
    )
    patch = mock_patch(base_url, "recordings", "rec-1")
    claimed_before_create = []

    def create(request):
        claimed_before_create.append(
            [b.get("hub_job_key") for b in bodies(patch)] == [request.headers["Idempotency-Key"]]
        )
        raise httpx.ReadTimeout("lost")

    respx.post(f"{HUB}/v1/jobs").mock(side_effect=create)

    await cycle(base_url, tmp_path)

    assert claimed_before_create == [True]


# ── recording_parts (re-dictation) ──────────────────────────────────────────

PART = {
    "id": "part-1", "recording": "rec-1", "seq": 2, "status": "pending",
    "audio": "file-9", "language": "en", "profile": "dictation",
}


def mock_part_batch_patch(base_url, *, updated=True):
    """The filtered batch PATCH of `write_part`; returns the updated rows (or none)."""
    return respx.patch(f"{base_url}/items/recording_parts").mock(
        side_effect=lambda request: httpx.Response(
            200, json={"data": [{"id": "part-1"}] if updated else []},
        )
    )


@respx.mock
@pytest.mark.anyio
async def test_pending_part_is_claimed_and_submitted_with_its_language_and_profile(
    base_url, tmp_path,
):
    mock_lists(pending={"recording_parts": [PART]})
    respx.get(f"{base_url}/assets/file-9").mock(return_value=httpx.Response(200, content=b"RIFF"))
    upload = respx.post(f"{HUB}/v1/uploads").mock(
        return_value=httpx.Response(201, json={"upload_id": "up-9", "sha256": "x", "size": 4})
    )
    job = respx.post(f"{HUB}/v1/jobs").mock(
        return_value=httpx.Response(202, json={"job_id": 11, "status": "pending"})
    )
    patch = mock_patch(base_url, "recording_parts", "part-1")

    assert await cycle(base_url, tmp_path, engine="whisper") == 1

    assert b'filename="part-part-1"' in upload.calls.last.request.content
    req = job.calls.last.request
    key = req.headers["Idempotency-Key"]
    assert key.startswith("recording_parts:part-1:")
    assert json.loads(req.content) == {
        "kind": "audio", "upload_id": "up-9", "private": True,
        "language": "en", "profile": "dictation", "engine": "whisper",
    }
    assert bodies(patch) == [
        {"status": "transcribing", "hub_job_id": None, "hub_job_key": key, "error_message": None},
        {"hub_job_id": 11, "error_message": None},
    ]
    assert list(tmp_path.iterdir()) == []


@respx.mock
@pytest.mark.anyio
async def test_completed_part_writes_its_text_and_no_transcript(base_url, tmp_path):
    row = {**PART, "status": "transcribing", "hub_job_id": 11, "hub_job_key": "k"}
    mock_lists(active={"recording_parts": [row]})
    respx.get(f"{HUB}/v1/jobs/11").mock(return_value=httpx.Response(200, json={
        "status": "completed", "error": None, "candidate": {"engine": "faster-whisper"},
    }))
    respx.get(f"{HUB}/v1/jobs/11/result").mock(return_value=httpx.Response(200, json={
        "markdown": "Neuer Absatz.", "language": "en", "segments": [], "provenance": PROV,
    }))
    delete = respx.delete(f"{HUB}/v1/jobs/11").mock(return_value=httpx.Response(204))
    write = mock_part_batch_patch(base_url)
    single = mock_patch(base_url, "recording_parts", "part-1")
    transcripts = respx.route(host="directus.test", path__startswith="/items/transcripts/")

    assert await cycle(base_url, tmp_path) == 1

    assert bodies(write) == [{
        "query": {"filter": {"id": {"_eq": "part-1"}, "status": {"_eq": "transcribing"}}},
        "data": {
            "text": "Neuer Absatz.", "status": "done",
            "hub_job_id": None, "hub_job_key": None, "error_message": None,
        },
    }]
    assert not single.called  # no separate mark_done, no title
    assert not transcripts.called
    assert delete.called


@respx.mock
@pytest.mark.anyio
async def test_part_discarded_while_its_job_ran_stays_discarded(base_url, tmp_path):
    row = {**PART, "status": "transcribing", "hub_job_id": 11}
    mock_lists(active={"recording_parts": [row]})
    respx.get(f"{HUB}/v1/jobs/11").mock(
        return_value=httpx.Response(200, json={"status": "completed", "error": None})
    )
    respx.get(f"{HUB}/v1/jobs/11/result").mock(
        return_value=httpx.Response(200, json={"markdown": "x"})
    )
    delete = respx.delete(f"{HUB}/v1/jobs/11").mock(return_value=httpx.Response(204))
    mock_part_batch_patch(base_url, updated=False)
    single = mock_patch(base_url, "recording_parts", "part-1")

    await cycle(base_url, tmp_path)

    assert not single.called
    assert delete.called


@respx.mock
@pytest.mark.anyio
async def test_failed_part_job_marks_error(base_url, tmp_path):
    row = {**PART, "status": "transcribing", "hub_job_id": 11}
    mock_lists(active={"recording_parts": [row]})
    respx.get(f"{HUB}/v1/jobs/11").mock(return_value=httpx.Response(200, json={
        "status": "failed", "error": {"code": "engine_error", "message": "kaputt"},
    }))
    patch = mock_patch(base_url, "recording_parts", "part-1")

    assert await cycle(base_url, tmp_path) == 1

    assert bodies(patch) == [{
        "status": "error", "error_message": "Verarbeitung fehlgeschlagen: kaputt",
        "hub_job_id": None, "hub_job_key": None,
    }]


@respx.mock
@pytest.mark.anyio
async def test_part_without_audio_marks_error(base_url, tmp_path):
    mock_lists(pending={"recording_parts": [{**PART, "audio": None}]})
    patch = mock_patch(base_url, "recording_parts", "part-1")

    await cycle(base_url, tmp_path)

    assert bodies(patch)[0]["status"] == "error"
    assert bodies(patch)[0]["error_message"] == "Datei fehlt"


@respx.mock
@pytest.mark.anyio
async def test_stale_part_without_job_goes_back_to_pending(base_url, tmp_path):
    lists = mock_lists(stale={"recording_parts": [{"id": "part-old"}]})
    patch = mock_patch(base_url, "recording_parts", "part-old")

    assert await cycle(base_url, tmp_path) == 1

    assert bodies(patch) == [{"status": "pending"}]
    stale = [
        c.request for c in lists.calls
        if c.request.url.path.endswith("/recording_parts")
        and "filter[date_updated][_lt]" in c.request.url.params
    ]
    assert stale[0].url.params["filter[status][_eq]"] == "transcribing"
    assert stale[0].url.params["filter[hub_job_id][_null]"] == "true"


@respx.mock
@pytest.mark.anyio
async def test_cleanup_deletes_part_audio_only_once_appended_and_settled(base_url, tmp_path):
    parts = [
        {"id": "p-done", "audio": "f-done", "offset_seconds": 0.0, "status": "done"},
        {"id": "p-applied", "audio": "f-applied", "offset_seconds": 12.5, "status": "applied"},
        {"id": "p-discarded", "audio": "f-discarded", "offset_seconds": 3.0, "status": "discarded"},
        # the page has not appended this audio yet
        {"id": "p-unappended", "audio": "f-unappended", "offset_seconds": None, "status": "applied"},
        # still being transcribed / failed: the audio may be needed again
        {"id": "p-active", "audio": "f-active", "offset_seconds": 1.0, "status": "transcribing"},
        {"id": "p-error", "audio": "f-error", "offset_seconds": 1.0, "status": "error"},
        {"id": "p-clean", "audio": None, "offset_seconds": 1.0, "status": "applied"},
    ]
    lists = mock_lists(cleanup=parts)
    deleted = respx.delete(host="directus.test", path__regex=r"^/files/(?P<fid>[^/]+)$").mock(
        side_effect=lambda request, fid: httpx.Response(404 if fid == "f-applied" else 204)
    )
    patches = {p["id"]: mock_patch(base_url, "recording_parts", p["id"]) for p in parts}

    assert await cycle(base_url, tmp_path) == 3

    assert sorted(c.request.url.path for c in deleted.calls) == [
        "/files/f-applied", "/files/f-discarded", "/files/f-done",
    ]
    for pid in ("p-done", "p-applied", "p-discarded"):
        assert bodies(patches[pid]) == [{"audio": None}]
    for pid in ("p-unappended", "p-active", "p-error", "p-clean"):
        assert not patches[pid].called
    query = [c.request for c in lists.calls if "filter[offset_seconds][_nnull]" in c.request.url.params][0]
    assert query.url.params["limit"] == "5"


@respx.mock
@pytest.mark.anyio
async def test_failed_audio_cleanup_keeps_the_part_and_never_stops_the_cycle(
    base_url, tmp_path, caplog,
):
    parts = [
        {"id": "p-1", "audio": "f-1", "offset_seconds": 1.0, "status": "applied"},
        {"id": "p-2", "audio": "f-2", "offset_seconds": 2.0, "status": "applied"},
    ]
    mock_lists(cleanup=parts)
    respx.delete(f"{base_url}/files/f-1").mock(return_value=httpx.Response(500))
    respx.delete(f"{base_url}/files/f-2").mock(return_value=httpx.Response(204))
    p1 = mock_patch(base_url, "recording_parts", "p-1")
    p2 = mock_patch(base_url, "recording_parts", "p-2")

    with caplog.at_level(logging.ERROR):
        assert await cycle(base_url, tmp_path) == 1

    assert not p1.called
    assert bodies(p2) == [{"audio": None}]
    assert "p-1" in caplog.text
