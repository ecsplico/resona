"""upload_bytes / submit_upload: in-memory uploads and jobs for an existing upload."""
import json

import httpx
import pytest
import respx

from resona_client.client import JobRef, PolicyDeniedError, ResonaClient, Upload

BASE = "http://localhost:7000"
WAV_HEAD = b"RIFF\x24\x00\x00\x00WAVEfmt "


@respx.mock
def test_upload_bytes_sends_content_as_multipart_file():
    route = respx.post(f"{BASE}/v1/uploads").mock(
        return_value=httpx.Response(201, json={"upload_id": "upl_1", "sha256": "ab", "size": 12})
    )
    up = ResonaClient(base_url=BASE, api_key="k").upload_bytes("clip.wav", WAV_HEAD)

    assert up == Upload(upload_id="upl_1", sha256="ab", size=12)
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer k"
    assert b'name="file"' in request.content
    assert b"Content-Type: audio/x-wav" in request.content or b"Content-Type: audio/wav" in request.content
    assert WAV_HEAD in request.content


@respx.mock
def test_submit_upload_posts_job_without_uploading_again():
    uploads = respx.post(f"{BASE}/v1/uploads")
    jobs = respx.post(f"{BASE}/v1/jobs").mock(
        return_value=httpx.Response(202, json={"job_id": 9, "status": "pending"})
    )
    ref = ResonaClient(base_url=BASE, api_key="k").submit_upload(
        "upl_1", kind="document", execution_profile="cpu-private",
        options={"ocr": "auto"}, idempotency_key="key-1",
    )

    assert ref == JobRef(job_id=9, status="pending")
    assert not uploads.called
    request = jobs.calls.last.request
    assert request.headers["Idempotency-Key"] == "key-1"
    assert json.loads(request.content) == {
        "kind": "document", "upload_id": "upl_1", "private": True,
        "execution_profile": "cpu-private", "options": {"ocr": "auto"},
    }


@respx.mock
def test_submit_upload_rejects_options_of_other_kind_before_any_request():
    jobs = respx.post(f"{BASE}/v1/jobs")
    with pytest.raises(ValueError):
        ResonaClient(base_url=BASE).submit_upload("upl_1", kind="audio", options={"ocr": "auto"})
    assert not jobs.called


@respx.mock
def test_submit_upload_reuses_key_on_retry_and_surfaces_policy_denial():
    jobs = respx.post(f"{BASE}/v1/jobs").mock(side_effect=[
        httpx.Response(503, json={"error": "unavailable", "message": "busy"}),
        httpx.Response(403, json={"error": "policy_denied", "message": "cloud disabled"}),
    ])
    with pytest.raises(PolicyDeniedError):
        ResonaClient(base_url=BASE).submit_upload("upl_1", kind="audio", private=False)
    keys = {call.request.headers["Idempotency-Key"] for call in jobs.calls}
    assert len(jobs.calls) == 2 and len(keys) == 1
