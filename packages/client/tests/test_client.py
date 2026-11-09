"""Tests for resona_client.client.ResonaClient using respx."""
import io
import json
import struct
import wave
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from resona_client.client import (
    AudioOptions,
    DocumentOptions,
    JobNotReady,
    JobRef,
    JobTimeout,
    PolicyDeniedError,
    ResonaAPIError,
    ResonaClient,
    RevisionConflict,
    ResultExpired,
    SettingsDoc,
    SyncWaitTimeout,
    UploadLink,
)
from resona_job_core import JobResult

BASE = "http://localhost:7000"


def _make_wav(path: Path) -> Path:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<" + "h" * 160, *([0] * 160)))
    path.write_bytes(buf.getvalue())
    return path


@pytest.fixture
def client():
    c = ResonaClient(base_url=BASE)
    yield c
    c.close()


@pytest.fixture
def audio_file(tmp_path):
    return _make_wav(tmp_path / "audio.wav")


# ── Constructor ───────────────────────────────────────────────────────────────

def test_init_reads_resona_env(monkeypatch):
    monkeypatch.delenv("WS_API_URL", raising=False)
    monkeypatch.delenv("WS_API_KEY", raising=False)
    monkeypatch.setenv("RESONA_API_URL", "http://myserver:8000")
    monkeypatch.setenv("RESONA_API_KEY", "testkey")
    c = ResonaClient()
    assert c.base_url == "http://myserver:8000"
    assert c.api_key == "testkey"
    c.close()


def test_init_falls_back_to_ws_env(monkeypatch):
    monkeypatch.delenv("RESONA_API_URL", raising=False)
    monkeypatch.delenv("RESONA_API_KEY", raising=False)
    monkeypatch.setenv("WS_API_URL", "http://myserver:8000")
    monkeypatch.setenv("WS_API_KEY", "wskey")
    c = ResonaClient()
    assert c.base_url == "http://myserver:8000"
    assert c.api_key == "wskey"
    c.close()


def test_resona_env_takes_priority_over_ws_env(monkeypatch):
    monkeypatch.setenv("RESONA_API_URL", "http://resona-server:9000")
    monkeypatch.setenv("WS_API_URL", "http://ws-server:8000")
    monkeypatch.setenv("RESONA_API_KEY", "resona-key")
    monkeypatch.setenv("WS_API_KEY", "ws-key")
    c = ResonaClient()
    assert c.base_url == "http://resona-server:9000"
    assert c.api_key == "resona-key"
    c.close()


def test_default_base_url_is_ipv4_localhost(monkeypatch):
    # uvicorn binds IPv4 0.0.0.0; resolving "localhost" to ::1 breaks the
    # connection, so the default must be the explicit IPv4 loopback.
    monkeypatch.delenv("RESONA_API_URL", raising=False)
    monkeypatch.delenv("WS_API_URL", raising=False)
    c = ResonaClient()
    assert c.base_url == "http://127.0.0.1:7000"
    c.close()


def test_init_strips_trailing_slash():
    c = ResonaClient(base_url="http://server:7000/")
    assert c.base_url == "http://server:7000"
    c.close()


def test_from_config_uses_resona_env_url(monkeypatch):
    monkeypatch.setenv("RESONA_API_URL", "http://envurl:7000")
    monkeypatch.delenv("WS_API_URL", raising=False)
    c = ResonaClient.from_config(auto_start=False)
    assert c.base_url == "http://envurl:7000"
    c.close()


def test_from_config_uses_ws_env_url_as_fallback(monkeypatch):
    monkeypatch.delenv("RESONA_API_URL", raising=False)
    monkeypatch.setenv("WS_API_URL", "http://envurl:7000")
    c = ResonaClient.from_config(auto_start=False)
    assert c.base_url == "http://envurl:7000"
    c.close()


def test_from_config_resona_env_takes_priority_over_ws_env(monkeypatch):
    monkeypatch.setenv("RESONA_API_URL", "http://resona-url:9000")
    monkeypatch.setenv("WS_API_URL", "http://ws-url:8000")
    c = ResonaClient.from_config(auto_start=False)
    assert c.base_url == "http://resona-url:9000"
    c.close()


# ── Jobs ──────────────────────────────────────────────────────────────────────

UPLOAD_JSON = {"upload_id": "up-1", "sha256": "ab" * 32, "size": 1}


def _job_json(job_id, status="pending", **extra):
    base = {
        "job_id": job_id,
        "kind": "audio",
        "status": status,
        "queue_reason": None,
        "private": True,
        "execution_profile": "private-auto",
        "candidate": None,
        "attempt": 0,
        "error": None,
        "result_available": False,
        "created_at": "2026-10-31T10:00:00Z",
        "updated_at": "2026-10-31T10:00:00Z",
        "deadline_at": "2026-11-01T10:00:00Z",
    }
    return base | extra


def test_submit_job_uploads_then_posts_job_with_bearer_and_idempotency_key(audio_file):
    c = ResonaClient(base_url=BASE, api_key="secret-key")
    with respx.mock as mock:
        respx.post(f"{BASE}/v1/uploads").mock(return_value=httpx.Response(201, json=UPLOAD_JSON))
        respx.post(f"{BASE}/v1/jobs").mock(
            return_value=httpx.Response(202, json={"job_id": 42, "status": "pending"})
        )
        ref = c.submit_job(audio_file, kind="audio", language="de", private=True)
        assert ref == JobRef(job_id=42, status="pending")
        assert [call.request.url.path for call in mock.calls] == ["/v1/uploads", "/v1/jobs"]
        for call in mock.calls:
            assert call.request.headers["Authorization"] == "Bearer secret-key"
        job_request = mock.calls[1].request
        assert job_request.headers["Idempotency-Key"]
        assert json.loads(job_request.content) == {
            "kind": "audio",
            "upload_id": "up-1",
            "private": True,
            "language": "de",
        }
    c.close()


def test_submit_text_job_sends_profile_and_rejects_media_options(tmp_path):
    text_file = tmp_path / "befund.md"
    text_file.write_text("Befund Komma unauffällig\n", encoding="utf-8")
    c = ResonaClient(base_url=BASE, api_key="k")
    with pytest.raises(ValueError):
        c.submit_job(text_file, kind="text", profile="fix", options=AudioOptions(translate=True))
    with respx.mock as mock:
        respx.post(f"{BASE}/v1/uploads").mock(return_value=httpx.Response(201, json=UPLOAD_JSON))
        respx.post(f"{BASE}/v1/jobs").mock(return_value=httpx.Response(202, json={"job_id": 5, "status": "pending"}))
        assert c.submit_job(text_file, kind="text", profile="fix").job_id == 5
        assert json.loads(mock.calls[1].request.content) == {
            "kind": "text", "upload_id": "up-1", "private": True, "profile": "fix",
        }
    c.close()


def test_submit_job_retries_transport_error_with_same_idempotency_key_and_upload(audio_file):
    c = ResonaClient(base_url=BASE, api_key="k")
    with respx.mock:
        upload = respx.post(f"{BASE}/v1/uploads").mock(
            return_value=httpx.Response(201, json=UPLOAD_JSON)
        )
        job = respx.post(f"{BASE}/v1/jobs").mock(side_effect=[
            httpx.ConnectError("connection reset"),
            httpx.Response(202, json={"job_id": 7, "status": "pending"}),
        ])
        with patch("resona_client.client.time.sleep"):
            ref = c.submit_job(audio_file, kind="audio")
    c.close()
    assert ref.job_id == 7
    assert upload.call_count == 1
    keys = [call.request.headers["Idempotency-Key"] for call in job.calls]
    assert len(keys) == 2
    assert keys[0] == keys[1]


def test_submit_job_maps_idempotency_conflict(audio_file, client):
    with respx.mock:
        respx.post(f"{BASE}/v1/uploads").mock(return_value=httpx.Response(201, json=UPLOAD_JSON))
        respx.post(f"{BASE}/v1/jobs").mock(return_value=httpx.Response(
            409, json={"error": "idempotency_conflict", "message": "key reused for another request"}))
        with pytest.raises(ResonaAPIError) as exc:
            client.submit_job(audio_file, kind="audio")
    assert exc.value.status == 409
    assert exc.value.code == "idempotency_conflict"
    assert not isinstance(exc.value, PolicyDeniedError)


def test_submit_job_maps_policy_denied(audio_file, client):
    with respx.mock:
        respx.post(f"{BASE}/v1/uploads").mock(return_value=httpx.Response(201, json=UPLOAD_JSON))
        respx.post(f"{BASE}/v1/jobs").mock(return_value=httpx.Response(
            403, json={"error": "policy_denied", "message": "cloud is not enabled"}))
        with pytest.raises(PolicyDeniedError) as exc:
            client.submit_job(audio_file, kind="audio", private=False)
    assert exc.value.status == 403
    assert exc.value.code == "policy_denied"


def test_submit_job_rejects_options_for_wrong_kind_before_uploading(audio_file, client):
    with respx.mock as mock:
        with pytest.raises(ValueError):
            client.submit_job(audio_file, kind="audio", options={"ocr": "force"})
        with pytest.raises(ValueError):
            client.submit_job(audio_file, kind="audio", options=DocumentOptions(ocr="force"))
        assert mock.calls.call_count == 0


def test_submit_job_sends_typed_options(audio_file, client):
    with respx.mock as mock:
        respx.post(f"{BASE}/v1/uploads").mock(return_value=httpx.Response(201, json=UPLOAD_JSON))
        respx.post(f"{BASE}/v1/jobs").mock(
            return_value=httpx.Response(202, json={"job_id": 1, "status": "pending"}))
        client.submit_job(audio_file, kind="audio", options=AudioOptions(translate=True))
        client.submit_job(
            audio_file, kind="document", execution_profile="mac-private",
            options=DocumentOptions(ocr="force", page_start=2, page_end=3),
        )
        audio_body = json.loads(mock.calls[1].request.content)
        doc_body = json.loads(mock.calls[3].request.content)
    assert audio_body["options"] == {"translate": True, "word_timestamps": False, "vad_filter": True}
    assert doc_body["kind"] == "document"
    assert doc_body["execution_profile"] == "mac-private"
    assert doc_body["options"] == {"ocr": "force", "page_start": 2, "page_end": 3}


def test_submit_job_uses_given_idempotency_key(audio_file, client):
    with respx.mock:
        respx.post(f"{BASE}/v1/uploads").mock(return_value=httpx.Response(201, json=UPLOAD_JSON))
        job = respx.post(f"{BASE}/v1/jobs").mock(
            return_value=httpx.Response(202, json={"job_id": 1, "status": "pending"}))
        client.submit_job(audio_file, kind="audio", idempotency_key="fixed-key")
    assert job.calls[0].request.headers["Idempotency-Key"] == "fixed-key"


def test_create_upload_link_posts_with_bearer_and_returns_typed_link():
    body = {
        "upload_url": "https://hub.example/v1/upload-links/upload",
        "token_header": "X-Resona-Upload-Token",
        "token": "upt_abc",
        "expires_at": "2026-11-04T10:10:00Z",
        "max_bytes": 209715200,
    }
    c = ResonaClient(base_url=BASE, api_key="secret-key")
    with respx.mock:
        route = respx.post(f"{BASE}/v1/upload-links").mock(return_value=httpx.Response(201, json=body))
        link = c.create_upload_link()
    c.close()
    assert route.calls[0].request.headers["Authorization"] == "Bearer secret-key"
    assert link == UploadLink(**body)


@pytest.mark.parametrize("status,code", [(401, "unauthorized"), (403, "forbidden")])
def test_create_upload_link_maps_auth_errors(client, status, code):
    with respx.mock:
        respx.post(f"{BASE}/v1/upload-links").mock(return_value=httpx.Response(
            status, json={"detail": {"error": code, "message": "no"}}))
        with pytest.raises(ResonaAPIError) as exc:
            client.create_upload_link()
    assert (exc.value.status, exc.value.code) == (status, code)


def test_get_job_returns_typed_view(client):
    with respx.mock:
        respx.get(f"{BASE}/v1/jobs/5").mock(
            return_value=httpx.Response(200, json=_job_json(5, "processing", attempt=1)))
        job = client.get_job(5)
    assert job.job_id == 5
    assert job.status == "processing"
    assert job.attempt == 1
    assert not job.terminal


def test_list_jobs_passes_cursor_and_limit(client):
    with respx.mock:
        route = respx.get(f"{BASE}/v1/jobs").mock(return_value=httpx.Response(
            200, json={"items": [_job_json(1)], "next_cursor": "c2"}))
        page = client.list_jobs(cursor="c1", limit=10)
    params = route.calls[0].request.url.params
    assert params["cursor"] == "c1"
    assert params["limit"] == "10"
    assert page.next_cursor == "c2"
    assert [j.job_id for j in page.items] == [1]


def test_wait_for_job_returns_terminal_view(client):
    responses = [
        httpx.Response(200, json=_job_json(1, "pending")),
        httpx.Response(200, json=_job_json(1, "completed", result_available=True)),
    ]
    with respx.mock:
        respx.get(f"{BASE}/v1/jobs/1").mock(side_effect=responses)
        with patch("resona_client.client.time.sleep"):
            job = client.wait_for_job(1, poll=0.0)
    assert job.status == "completed"
    assert job.terminal


def test_wait_for_job_timeout_raises_job_timeout(client):
    with respx.mock:
        respx.get(f"{BASE}/v1/jobs/3").mock(
            return_value=httpx.Response(200, json=_job_json(3, "pending")))
        with pytest.raises(JobTimeout) as exc:
            client.wait_for_job(3, timeout=0.0, poll=0.0)
    assert exc.value.job_id == 3
    assert isinstance(exc.value, TimeoutError)


def test_get_result_returns_job_result(client):
    body = {
        "schema_version": 1, "kind": "document", "raw_text": "raw", "markdown": "# Titel",
        "language": "de", "segments": [], "pages": [{"number": 1, "text": "raw"}], "warnings": [],
        "structured": {}, "provenance": {"stages": []},
    }
    with respx.mock:
        respx.get(f"{BASE}/v1/jobs/9/result").mock(return_value=httpx.Response(200, json=body))
        result = client.get_result(9)
    assert isinstance(result, JobResult)
    assert result.markdown == "# Titel"
    assert result.raw_text == "raw"
    assert [page.number for page in result.pages] == [1]


def test_get_result_maps_not_ready_and_expired(client):
    with respx.mock:
        respx.get(f"{BASE}/v1/jobs/9/result").mock(return_value=httpx.Response(
            409, json={"error": "not_ready", "message": "job is still processing"}))
        with pytest.raises(JobNotReady):
            client.get_result(9)
        respx.get(f"{BASE}/v1/jobs/9/result").mock(return_value=httpx.Response(
            410, json={"error": "expired", "message": "result was removed"}))
        with pytest.raises(ResultExpired):
            client.get_result(9)


def test_get_result_markdown_reads_one_range(client):
    body = {"text": "itel", "offset": 3, "next_offset": 7, "total_chars": 12, "kind": "document",
            "language": "de", "page_count": 2, "warnings": ["w"], "provenance": {"stages": []}}
    with respx.mock:
        route = respx.get(f"{BASE}/v1/jobs/9/result/markdown").mock(
            return_value=httpx.Response(200, json=body))
        chunk = client.get_result_markdown(9, offset=3, limit=4)
    assert dict(route.calls.last.request.url.params) == {"offset": "3", "limit": "4"}
    assert (chunk.text, chunk.offset, chunk.next_offset, chunk.total_chars) == ("itel", 3, 7, 12)
    assert (chunk.kind, chunk.page_count, chunk.warnings) == ("document", 2, ("w",))


def test_get_result_markdown_maps_not_ready_and_expired(client):
    with respx.mock:
        respx.get(f"{BASE}/v1/jobs/9/result/markdown").mock(return_value=httpx.Response(
            409, json={"error": "not_ready", "message": "job is still processing"}))
        with pytest.raises(JobNotReady):
            client.get_result_markdown(9)
        respx.get(f"{BASE}/v1/jobs/9/result/markdown").mock(return_value=httpx.Response(
            410, json={"error": "expired", "message": "result was removed"}))
        with pytest.raises(ResultExpired):
            client.get_result_markdown(9)


def test_promote_evaluation_sends_rebaseline_and_maps_corpus_conflict(client):
    with respx.mock:
        route = respx.post(f"{BASE}/v1/evaluations/4/promote").mock(return_value=httpx.Response(
            409, json={"error": "baseline_corpus_mismatch", "message": "corpus changed"}))
        with pytest.raises(RevisionConflict) as exc:
            client.promote_evaluation(4, expected_baseline_revision=2)
        assert exc.value.code == "baseline_corpus_mismatch"
        assert json.loads(route.calls.last.request.content) == {
            "expected_baseline_revision": 2, "rebaseline": False}
        route.mock(return_value=httpx.Response(200, json={"baseline_revision": 3}))
        assert client.promote_evaluation(4, expected_baseline_revision=2, rebaseline=True) == {
            "baseline_revision": 3}
        assert json.loads(route.calls.last.request.content)["rebaseline"] is True


def test_cancel_and_delete_job(client):
    with respx.mock:
        respx.post(f"{BASE}/v1/jobs/5/cancel").mock(
            return_value=httpx.Response(200, json=_job_json(5, "cancelled")))
        respx.delete(f"{BASE}/v1/jobs/5").mock(return_value=httpx.Response(204))
        assert client.cancel_job(5).status == "cancelled"
        assert client.delete_job(5) is None


def test_get_settings_returns_settings_doc(client):
    with respx.mock:
        respx.get(f"{BASE}/v1/settings").mock(return_value=httpx.Response(
            200, headers={"ETag": '"4"'},
            json={"organization_id": "smoke-a", "revision": 4, "settings": {"language": "de"}}))
        doc = client.get_settings()
    assert doc == SettingsDoc(
        organization_id="smoke-a", revision=4, etag='"4"', settings={"language": "de"})


def test_put_settings_sends_if_match_and_maps_revision_conflict(client):
    with respx.mock:
        route = respx.put(f"{BASE}/v1/settings").mock(return_value=httpx.Response(
            409, json={"error": "revision_conflict", "message": "current revision is 5"}))
        with pytest.raises(RevisionConflict) as exc:
            client.put_settings({"language": "en"}, if_match='"3"')
    assert route.calls[0].request.headers["If-Match"] == '"3"'
    assert json.loads(route.calls[0].request.content) == {"language": "en"}
    assert exc.value.status == 409
    assert exc.value.code == "revision_conflict"


def test_put_settings_returns_new_revision(client):
    with respx.mock:
        respx.put(f"{BASE}/v1/settings").mock(return_value=httpx.Response(
            200, headers={"ETag": '"5"'},
            json={"organization_id": "smoke-a", "revision": 5, "settings": {"language": "en"}}))
        doc = client.put_settings({"language": "en"}, if_match='"4"')
    assert (doc.revision, doc.etag, doc.settings) == (5, '"5"', {"language": "en"})


# ── Profiles ──────────────────────────────────────────────────────────────────

@respx.mock
def test_list_profiles():
    respx.get("http://t/profiles").mock(return_value=httpx.Response(
        200, json={"profiles": [{"name": "a", "description": "AA"}]}))
    c = ResonaClient(base_url="http://t")
    assert c.list_profiles() == [{"name": "a", "description": "AA"}]


@respx.mock
def test_put_profile():
    route = respx.put("http://t/profiles/a").mock(return_value=httpx.Response(200, json={}))
    ResonaClient(base_url="http://t").put_profile("a", {"name": "a", "steps": []})
    assert route.called




# ── Context manager ───────────────────────────────────────────────────────────

def test_context_manager():
    with ResonaClient(base_url=BASE) as c:
        assert c.base_url == BASE


# ── v1 Audio & Engine routes ──────────────────────────────────────────────────

def test_list_engines(client):
    with respx.mock:
        respx.get(f"{BASE}/v1/engines").mock(
            return_value=httpx.Response(200, json={
                "engines": [{"name": "faster-whisper", "kind": "local",
                              "capabilities": ["stt"], "private": True,
                              "available": True, "models": ["large-v3"]}],
                "default": "faster-whisper",
            })
        )
        result = client.list_engines()
    assert "engines" in result
    assert result["engines"][0]["name"] == "faster-whisper"


def test_list_listen_engines(client):
    with respx.mock:
        respx.get(f"{BASE}/v1/listen/engines").mock(
            return_value=httpx.Response(200, json={
                "engines": [{"name": "faster-whisper", "kind": "local",
                              "private": True, "models": ["large-v3"]}],
                "default": "faster-whisper",
            })
        )
        result = client.list_listen_engines()
    assert result["default"] == "faster-whisper"
    assert result["engines"][0]["name"] == "faster-whisper"


def test_create_transcription(client, audio_file):
    with respx.mock:
        respx.post(f"{BASE}/v1/audio/transcriptions").mock(
            return_value=httpx.Response(200, json={
                "text": "hello world", "language": "de", "segments": []
            })
        )
        result = client.create_transcription(audio_file, language="de")
    assert result["text"] == "hello world"


def test_create_speech(client):
    fake_audio = b"\xff\xfb\x90\x00" * 100  # fake mp3 bytes
    with respx.mock:
        respx.post(f"{BASE}/v1/audio/speech").mock(
            return_value=httpx.Response(200, content=fake_audio,
                                        headers={"content-type": "audio/mpeg"})
        )
        result = client.create_speech("hello", voice="nova")
    assert isinstance(result, bytes)
    assert len(result) > 0


def test_create_transcription_sends_private_true_by_default(client, audio_file):
    with respx.mock:
        route = respx.post(f"{BASE}/v1/audio/transcriptions").mock(
            return_value=httpx.Response(200, json={"text": "hallo"}))
        client.create_transcription(audio_file)
        client.create_transcription(audio_file, private=False)
    assert b'name="private"\r\n\r\ntrue' in route.calls[0].request.content
    assert b'name="private"\r\n\r\nfalse' in route.calls[1].request.content


def test_create_transcription_timeout_carries_job_id(client, audio_file):
    with respx.mock:
        respx.post(f"{BASE}/v1/audio/transcriptions").mock(return_value=httpx.Response(
            504, json={"error": "timeout", "message": "still running", "job_id": 17},
            headers={"Location": "/v1/jobs/17"}))
        with pytest.raises(SyncWaitTimeout) as exc:
            client.create_transcription(audio_file, execution_profile="cpu-private")
    assert exc.value.job_id == 17


def test_create_transcription_text_format_returns_text(client, audio_file):
    with respx.mock:
        respx.post(f"{BASE}/v1/audio/transcriptions").mock(
            return_value=httpx.Response(200, text="nur text"))
        assert client.create_transcription(audio_file, response_format="text") == {"text": "nur text"}


# ── Testbench ─────────────────────────────────────────────────────────────────

@respx.mock
def test_testbench_contribute_posts_multipart(client, audio_file):
    route = respx.post(f"{BASE}/v1/testbench/items").mock(
        return_value=httpx.Response(201, json={"id": 4, "status": "awaiting_draft"}))

    item = client.testbench_contribute(audio_file, language="de", label="memo")

    assert item["id"] == 4
    body = route.calls.last.request.content
    assert b'name="language"' in body and b'name="label"' in body and b"audio.wav" in body


@respx.mock
def test_testbench_disabled_surfaces_code(client, audio_file):
    respx.post(f"{BASE}/v1/testbench/items").mock(
        return_value=httpx.Response(403, json={"error": "testbench_disabled", "message": "off"}))

    with pytest.raises(ResonaAPIError) as exc:
        client.testbench_contribute(audio_file)

    assert exc.value.status == 403 and exc.value.code == "testbench_disabled"


@respx.mock
def test_testbench_list_items_sends_only_given_params(client):
    route = respx.get(f"{BASE}/v1/testbench/items").mock(
        return_value=httpx.Response(200, json={"items": [], "next_cursor": None}))

    client.testbench_list_items(status="corrected", limit=5)

    assert dict(route.calls.last.request.url.params) == {"status": "corrected", "limit": "5"}


@respx.mock
def test_testbench_reference_run_and_delete(client):
    ref = respx.put(f"{BASE}/v1/testbench/items/3/reference").mock(
        return_value=httpx.Response(200, json={"id": 3, "reference": "hallo"}))
    run = respx.post(f"{BASE}/v1/testbench/items/3/runs").mock(
        return_value=httpx.Response(201, json={"items": [{"id": 9}]}))
    delete = respx.delete(f"{BASE}/v1/testbench/items/3").mock(return_value=httpx.Response(204))
    cand = {"engine": "faster-whisper", "model": "m", "accelerator": "cpu"}

    assert client.testbench_set_reference(3, "hallo")["reference"] == "hallo"
    assert json.loads(ref.calls.last.request.content) == {"text": "hallo"}
    assert client.testbench_run(3, [cand])["items"][0]["id"] == 9
    assert json.loads(run.calls.last.request.content) == {"candidates": [cand]}
    assert client.testbench_delete_item(3) is None
    assert delete.called


@respx.mock
def test_testbench_read_routes(client):
    respx.get(f"{BASE}/v1/testbench/status").mock(return_value=httpx.Response(200, json={"enabled": True}))
    respx.get(f"{BASE}/v1/testbench/candidates").mock(return_value=httpx.Response(200, json={"items": []}))
    respx.get(f"{BASE}/v1/testbench/items/3").mock(return_value=httpx.Response(200, json={"id": 3}))
    respx.get(f"{BASE}/v1/testbench/items/3/runs/9").mock(return_value=httpx.Response(200, json={"id": 9}))
    respx.get(f"{BASE}/v1/testbench/leaderboard").mock(return_value=httpx.Response(200, json={"items": []}))

    assert client.testbench_status()["enabled"] is True
    assert client.testbench_candidates() == {"items": []}
    assert client.testbench_get_item(3)["id"] == 3
    assert client.testbench_get_run(3, 9)["id"] == 9
    assert client.testbench_leaderboard() == {"items": []}
