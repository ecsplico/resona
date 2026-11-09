"""Re-processing a stored transcript as a hub text job, against a fake hub + Directus."""
import json

import httpx
import pytest
import respx

from resona_project_worker.directus import DirectusClient
from resona_project_worker.hub import HubClient
from resona_project_worker.worker import run_once

HUB = "http://hub.test"
DIRECTUS = "http://directus.test"

RAW = "Kapitel Befund Absatz unauffällig Punkt"
MARKDOWN = "## Befund\n\nunauffällig."


class FakeDirectus:
    """In-memory `transcripts` (recordings/documents empty) with Directus list filters."""

    def __init__(self, *rows):
        self.rows = {row["id"]: dict(row) for row in rows}
        self.patches: list[dict] = []
        respx.get(
            host="directus.test", path__regex=r"^/items/(?P<collection>recordings|documents)$",
        ).mock(return_value=httpx.Response(200, json={"data": []}))
        respx.get(f"{DIRECTUS}/items/transcripts").mock(side_effect=self._list)
        respx.patch(host="directus.test", path__regex=r"^/items/transcripts/(?P<tid>[^/]+)$").mock(
            side_effect=self._patch,
        )

    def _list(self, request):
        params = request.url.params
        if "filter[date_updated][_lt]" in params:
            return httpx.Response(200, json={"data": []})
        status = params["filter[postprocess_status][_eq]"]
        rows = [r for r in self.rows.values() if r.get("postprocess_status") == status]
        if params.get("filter[postprocess_job_id][_nnull]"):
            rows = [r for r in rows if r.get("postprocess_job_id") is not None]
        return httpx.Response(200, json={"data": [dict(r) for r in rows]})

    def _patch(self, request, tid):
        body = json.loads(request.content)
        self.patches.append(body)
        self.rows[tid].update(body)
        return httpx.Response(200, json={"data": dict(self.rows[tid])})


class FakeHub:
    """Uploads + text jobs; `queue` is the queue_reason sequence of polls before completion."""

    def __init__(self, *, queue=(), result=MARKDOWN, create_error=None, job_error=None):
        self.uploads: dict[str, bytes] = {}
        self.jobs: dict[int, dict] = {}
        self.queue = list(queue)
        self.result = result
        self.create_error = create_error
        self.job_error = job_error
        self.deleted: list[int] = []
        self.upload_route = respx.post(f"{HUB}/v1/uploads").mock(side_effect=self._upload)
        self.create_route = respx.post(f"{HUB}/v1/jobs").mock(side_effect=self._create)
        respx.get(host="hub.test", path__regex=r"^/v1/jobs/(?P<jid>\d+)$").mock(side_effect=self._get)
        respx.get(host="hub.test", path__regex=r"^/v1/jobs/(?P<jid>\d+)/result$").mock(
            side_effect=self._result,
        )
        respx.delete(host="hub.test", path__regex=r"^/v1/jobs/(?P<jid>\d+)$").mock(
            side_effect=self._delete,
        )

    def _upload(self, request):
        upload_id = f"up-{len(self.uploads) + 1}"
        self.uploads[upload_id] = request.content
        return httpx.Response(201, json={"upload_id": upload_id, "kind": "text"})

    def _create(self, request):
        if self.create_error is not None:
            return self.create_error
        job_id = 100 + len(self.jobs)
        self.jobs[job_id] = {"body": json.loads(request.content), "key": request.headers["Idempotency-Key"]}
        return httpx.Response(202, json={"job_id": job_id, "status": "pending"})

    def _get(self, request, jid):
        if self.job_error is not None:
            return httpx.Response(200, json={"status": "failed", "error": self.job_error, "queue_reason": None})
        if self.queue:
            return httpx.Response(200, json={"status": "pending", "error": None, "queue_reason": self.queue.pop(0)})
        return httpx.Response(200, json={"status": "completed", "error": None, "queue_reason": None})

    def _result(self, request, jid):
        return httpx.Response(200, json={"markdown": self.result, "raw_text": RAW, "language": "de"})

    def _delete(self, request, jid):
        self.deleted.append(int(jid))
        return httpx.Response(204)


def transcript(**extra):
    return {
        "id": "t-1", "recording": "rec-1", "text": RAW, "raw_text": None, "applied_profile": None,
        "postprocess_status": "pending", "postprocess_profile": "transcript-markdown-de",
        "postprocess_error": None, "postprocess_queue_reason": None,
        "postprocess_job_id": None, "postprocess_job_key": None, **extra,
    }


async def cycle(tmp_path):
    directus = DirectusClient(DIRECTUS, "svc")
    hub = HubClient(HUB, "hub-tok")
    try:
        return await run_once(directus, hub, tmp_dir=tmp_path, batch=5, stale_minutes=15)
    finally:
        await directus.aclose()
        await hub.aclose()


@respx.mock
@pytest.mark.anyio
async def test_text_job_round_trip_keeps_raw_transcript(tmp_path):
    cms = FakeDirectus(transcript(raw_text=RAW))
    hub = FakeHub(queue=["no_capable_worker", "waiting_capacity"])

    assert await cycle(tmp_path) == 1  # submitted, then polled in the same cycle
    row = cms.rows["t-1"]
    assert row["postprocess_status"] == "processing"
    assert row["postprocess_job_id"] == 100
    upload = hub.uploads["up-1"]
    assert RAW.encode() in upload
    assert b'filename="transcript.md"' in upload
    assert hub.jobs[100]["body"] == {
        "kind": "text", "upload_id": "up-1", "private": True, "profile": "transcript-markdown-de",
    }
    assert hub.jobs[100]["key"] == row["postprocess_job_key"]
    assert hub.jobs[100]["key"].startswith("transcripts:t-1:")
    # Pending on the hub: the queue reason reaches the row, the job keeps running.
    assert row["postprocess_queue_reason"] == "no_capable_worker"

    assert await cycle(tmp_path) == 0
    assert row["postprocess_status"] == "processing"
    assert row["postprocess_queue_reason"] == "waiting_capacity"
    patches = len(cms.patches)

    assert await cycle(tmp_path) == 1  # completed
    assert cms.patches[patches:] == [{
        "text": MARKDOWN, "raw_text": RAW, "applied_profile": "transcript-markdown-de",
        "postprocess_status": "done", "postprocess_error": None,
        "postprocess_queue_reason": None, "postprocess_job_id": None, "postprocess_job_key": None,
    }]
    assert hub.deleted == [100]


@respx.mock
@pytest.mark.anyio
async def test_rerun_works_from_raw_text_not_the_processed_text(tmp_path):
    cms = FakeDirectus(transcript(
        text=MARKDOWN, raw_text=RAW, applied_profile="transcript-markdown-de",
        postprocess_profile="transcript-polish-de",
    ))
    hub = FakeHub(result="## Befund\n\nUnauffällig.")

    await cycle(tmp_path)
    await cycle(tmp_path)

    assert RAW.encode() in hub.uploads["up-1"]
    assert MARKDOWN.encode() not in hub.uploads["up-1"]
    row = cms.rows["t-1"]
    assert row["text"] == "## Befund\n\nUnauffällig."
    assert row["raw_text"] == RAW
    assert row["applied_profile"] == "transcript-polish-de"


@respx.mock
@pytest.mark.anyio
async def test_first_run_without_raw_text_keeps_text_as_raw(tmp_path):
    cms = FakeDirectus(transcript())
    FakeHub()

    await cycle(tmp_path)
    await cycle(tmp_path)

    assert cms.rows["t-1"]["raw_text"] == RAW
    assert cms.rows["t-1"]["text"] == MARKDOWN


@respx.mock
@pytest.mark.anyio
@pytest.mark.parametrize(("reply", "message"), [
    (httpx.Response(422, json={"error": "profile_required", "message": "text jobs need a postprocess profile name"}),
     "Kein Nachbearbeitungsprofil gewählt."),
    (httpx.Response(404, json={"error": "profile_not_found", "message": "no such profile"}),
     "Profil auf dem Hub nicht gefunden."),
    (httpx.Response(403, json={"error": "policy_denied", "message": "llm stage needs cloud_llm"}),
     "Von der Richtlinie der Organisation abgelehnt: llm stage needs cloud_llm"),
], ids=["profile_required", "profile_not_found", "policy_denied"])
async def test_create_errors_are_surfaced_in_german(tmp_path, reply, message):
    cms = FakeDirectus(transcript())
    FakeHub(create_error=reply)

    assert await cycle(tmp_path) == 1

    row = cms.rows["t-1"]
    assert row["postprocess_status"] == "error"
    assert row["postprocess_error"] == message
    assert row["postprocess_job_id"] is None
    assert row["postprocess_job_key"] is None
    assert row["text"] == RAW  # the transcript itself is untouched


@respx.mock
@pytest.mark.anyio
async def test_job_failing_on_policy_is_surfaced(tmp_path):
    cms = FakeDirectus(transcript(postprocess_status="processing", postprocess_job_id=100))
    FakeHub(job_error={"code": "policy_denied", "message": "endpoint local-llm not allowed"})

    await cycle(tmp_path)

    row = cms.rows["t-1"]
    assert row["postprocess_status"] == "error"
    assert row["postprocess_error"] == (
        "Von der Richtlinie der Organisation abgelehnt: endpoint local-llm not allowed"
    )


@respx.mock
@pytest.mark.anyio
async def test_transient_create_failure_leaves_row_for_stale_recovery(tmp_path):
    cms = FakeDirectus(transcript())
    FakeHub(create_error=httpx.Response(503, json={"error": "unavailable"}))

    assert await cycle(tmp_path) == 0

    row = cms.rows["t-1"]
    assert row["postprocess_status"] == "processing"
    assert row["postprocess_job_id"] is None
    assert row["postprocess_job_key"].startswith("transcripts:t-1:")
    assert row["postprocess_error"] is None


@respx.mock
@pytest.mark.anyio
async def test_empty_transcript_fails_without_calling_the_hub(tmp_path):
    cms = FakeDirectus(transcript(text="  \n"))
    hub = FakeHub()

    await cycle(tmp_path)

    assert cms.rows["t-1"]["postprocess_status"] == "error"
    assert cms.rows["t-1"]["postprocess_error"] == "Kein Transkript-Text zum Nachbearbeiten."
    assert not hub.upload_route.called
