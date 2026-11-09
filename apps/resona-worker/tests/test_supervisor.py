"""Supervisor job lifecycle against the fake hub and a real stub subprocess."""

from __future__ import annotations

import hashlib
import json

from resona_job_core import Candidate, JobResult

CAND = Candidate(engine="faster-whisper", model="tiny", model_revision="rev1", accelerator="cpu")


def test_completed_job_uploads_validated_result_with_matching_digest(build_supervisor, fakes) -> None:
    hub = fakes.FakeHub(CAND)
    supervisor = build_supervisor(hub, capabilities=(CAND,))

    assert supervisor.run(once=True) == 0

    uploads = hub.paths("/result")
    assert len(uploads) == 1
    _, _, headers, body = uploads[0]
    assert headers["X-Resona-Result-Sha256"] == hashlib.sha256(body).hexdigest()
    assert headers["X-Resona-Lease"] == "lease-abc"

    result = JobResult.model_validate_json(body)
    assert result.kind == "audio"
    assert result.raw_text == "hallo welt"
    assert result.provenance.worker_id == "w1"
    assert result.provenance.input_sha256 == hashlib.sha256(hub.job_input).hexdigest()
    assert hub.paths("/fail") == []


def test_unsupported_input_is_reported_as_terminal_failure(build_supervisor, fakes) -> None:
    hub = fakes.FakeHub(CAND, job_input=b"BADINPUT")
    supervisor = build_supervisor(hub, capabilities=(CAND,))

    assert supervisor.run(once=True) == 0

    ((_, _, _, body),) = hub.paths("/fail")
    failure = json.loads(body)
    assert failure["code"] == "unsupported_input"
    assert failure["retryable"] is False
    assert failure["withdraw_capability"] is None
    assert hub.paths("/result") == []


def test_out_of_memory_withdraws_capability_for_later_claims(build_supervisor, fakes) -> None:
    hub = fakes.FakeHub(CAND, job_input=b"OOM")
    supervisor = build_supervisor(hub, capabilities=(CAND,))

    supervisor.run(once=True)

    ((_, _, _, body),) = hub.paths("/fail")
    failure = json.loads(body)
    assert failure["code"] == "out_of_memory"
    assert failure["retryable"] is True
    assert failure["withdraw_capability"] == CAND.model_dump(mode="json")

    hub.claimed = False
    hub.job_input = b"OK"
    supervisor.run(once=True)

    assert hub.heartbeat_bodies[-1]["reported_capabilities"] == []
    assert hub.heartbeat_bodies[-1]["ready"] is False


def test_metal_allocator_failure_is_out_of_memory_and_only_that_tuple_is_withdrawn(build_supervisor, fakes) -> None:
    other = Candidate(engine="docling", model="docling-standard", accelerator="metal", ocr_engine="ocrmac")
    hub = fakes.FakeHub(CAND, job_input=b"METALOOM")
    hub.approved_capabilities = [CAND, other]
    supervisor = build_supervisor(hub, capabilities=(CAND, other))

    supervisor.run(once=True)

    ((_, _, _, body),) = hub.paths("/fail")
    failure = json.loads(body)
    assert (failure["code"], failure["retryable"]) == ("out_of_memory", True)
    assert failure["withdraw_capability"] == CAND.model_dump(mode="json")

    supervisor.run(once=True)

    assert hub.heartbeat_bodies[-1]["reported_capabilities"] == [other.model_dump(mode="json")]
    claim_body = json.loads(hub.paths("/claim")[-1][3])
    assert claim_body["capabilities"] == [other.model_dump(mode="json")]


def test_plain_engine_failure_is_retryable_without_withdrawal(build_supervisor, fakes) -> None:
    hub = fakes.FakeHub(CAND, job_input=b"ENGINE")
    supervisor = build_supervisor(hub, capabilities=(CAND,))

    supervisor.run(once=True)

    ((_, _, _, body),) = hub.paths("/fail")
    failure = json.loads(body)
    assert (failure["code"], failure["retryable"], failure["withdraw_capability"]) == ("engine_error", True, None)


def test_claims_only_tuples_both_tested_here_and_approved_by_the_hub(build_supervisor, fakes) -> None:
    untested = Candidate(engine="whisper", model="large-v3", accelerator="cuda")
    unapproved = Candidate(engine="faster-whisper", model="large-v3", accelerator="cpu")
    hub = fakes.FakeHub(CAND)
    hub.approved_capabilities = [CAND, untested]
    supervisor = build_supervisor(hub, capabilities=(CAND, unapproved))

    supervisor.run(once=True)

    reported = hub.heartbeat_bodies[-1]["reported_capabilities"]
    assert reported == [CAND.model_dump(mode="json"), unapproved.model_dump(mode="json")]
    claim_body = json.loads(hub.paths("/claim")[-1][3])
    assert claim_body["capabilities"] == [CAND.model_dump(mode="json")]


def test_tuples_on_an_unavailable_accelerator_are_never_offered(build_supervisor, fakes, monkeypatch) -> None:
    # A CUDA worker started without a GPU keeps its prepared manifest but offers no CUDA tuple,
    # so the hub leaves those jobs queued for other workers (e.g. the Mac) instead of failing them here.
    cuda = Candidate(engine="whisper", model="large-v3", accelerator="cuda")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    hub = fakes.FakeHub(cuda)
    hub.approved_capabilities = [CAND, cuda]
    supervisor = build_supervisor(hub, capabilities=(CAND, cuda))

    supervisor.run(once=True)

    assert hub.heartbeat_bodies[-1]["reported_capabilities"] == [CAND.model_dump(mode="json")]
    claim_body = json.loads(hub.paths("/claim")[-1][3])
    assert claim_body["capabilities"] == [CAND.model_dump(mode="json")]
    assert hub.paths("/fail") == []


def test_only_cuda_tuples_drop_out_when_the_gpu_is_missing(build_supervisor, fakes, monkeypatch, caplog) -> None:
    cuda = Candidate(engine="whisper", model="large-v3", accelerator="cuda")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    hub = fakes.FakeHub(cuda)
    hub.approved_capabilities = [cuda]
    hub.claimed = True  # the hub has no text job for it
    supervisor = build_supervisor(hub, capabilities=(cuda,))

    assert supervisor.run(once=True) == 0

    assert hub.heartbeat_bodies[-1] == {**hub.heartbeat_bodies[-1], "reported_capabilities": [], "ready": False}
    # Without a usable tuple it still polls, for text (postprocess-only) jobs only.
    ((_, _, _, claim_body),) = hub.paths("/claim")
    assert json.loads(claim_body) == {"capabilities": [], "text_jobs": True}
    assert [r.getMessage() for r in caplog.records].count(
        "no usable capability tuple on this host; serving text (postprocess-only) jobs only") == 1


def test_private_job_runs_offline_without_cloud_credentials(build_supervisor, fakes, monkeypatch) -> None:
    secrets = {
        "OPENAI_API_KEY": "sk-1",
        "OPENAI_BASE_URL": "https://api.openai.example",
        "DEEPGRAM_API_KEY": "dg-1",
        "ELEVENLABS_API_KEY": "el-1",
        "ANTHROPIC_API_KEY": "an-1",
        "AWS_SECRET_ACCESS_KEY": "aws-1",
        "AZURE_OPENAI_ENDPOINT": "https://azure.example",
        "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/g.json",
        "RESONA_API_KEY": "rsn-1",
        "HF_TOKEN": "hf-1",
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    hub = fakes.FakeHub(CAND, job_input=b"ENV")
    supervisor = build_supervisor(hub, capabilities=(CAND,))

    supervisor.run(once=True)

    ((_, _, _, body),) = hub.paths("/result")
    seen = json.loads(JobResult.model_validate_json(body).raw_text)
    assert not set(secrets) & set(seen)
    assert not any(value in seen.values() for value in secrets.values())
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK"):
        assert seen[name] == "1"


def test_lost_lease_kills_the_subprocess_and_uploads_nothing(build_supervisor, fakes) -> None:
    hub = fakes.FakeHub(CAND, job_input=b"HANG")
    hub.job_heartbeat_status = 409
    supervisor = build_supervisor(hub, capabilities=(CAND,), lease_heartbeat_seconds=0.01)

    supervisor.run(once=True)

    assert hub.paths("/result") == []
    assert hub.paths("/fail") == []


def test_stale_result_upload_is_discarded_without_failing_the_worker(build_supervisor, fakes) -> None:
    hub = fakes.FakeHub(CAND)
    hub.result_status = 409
    supervisor = build_supervisor(hub, capabilities=(CAND,))

    assert supervisor.run(once=True) == 0
    assert len(hub.paths("/result")) == 1
    assert hub.paths("/fail") == []


def test_private_cloud_postprocess_is_denied_before_any_hub_or_provider_call(build_supervisor, fakes) -> None:
    profile = {
        "name": "polish",
        "steps": [{"type": "llm", "name": "polish", "prompt": "Clean up.", "endpoint": "cloud-llm"}],
    }
    hub = fakes.FakeHub(CAND, profile=profile)
    supervisor = build_supervisor(hub, capabilities=(CAND,))

    supervisor.run(once=True)

    ((_, _, _, body),) = hub.paths("/fail")
    assert json.loads(body)["code"] == "policy_denied"
    assert hub.paths("/policy") == []
    assert hub.paths("/result") == []


def test_hub_refusal_at_an_external_stage_fails_the_job(build_supervisor, fakes) -> None:
    profile = {
        "name": "polish",
        "steps": [{"type": "llm", "name": "polish", "prompt": "Clean up.", "endpoint": "local-llm"}],
    }
    policy = {
        "private": True,
        "cloud_enabled": False,
        "approved_endpoint_ids": ["local-llm"],
        "endpoints": [
            {
                "id": "local-llm",
                "kind": "local_llm",
                "model": "qwen-local",
                "url": "http://127.0.0.1:9/v1",
                "worker_id": "w1",
            }
        ],
    }
    hub = fakes.FakeHub(CAND, snapshot=fakes.snapshot_for(CAND, policy=policy), profile=profile)
    hub.policy_status = 403
    hub.policy_body = {"error": "policy_denied", "message": "endpoint revoked"}
    supervisor = build_supervisor(hub, capabilities=(CAND,))

    supervisor.run(once=True)

    ((_, _, _, policy_body),) = hub.paths("/policy")
    assert json.loads(policy_body) == {"stage": "polish", "endpoint_id": "local-llm"}
    ((_, _, _, body),) = hub.paths("/fail")
    assert json.loads(body)["code"] == "policy_denied"
    assert hub.paths("/result") == []


REPLACE_PROFILE = {
    "name": "fix",
    "steps": [{"type": "replacements", "name": "fix", "rules": [{"pattern": "welt", "replacement": "Welt"}]}],
}


def test_postprocess_profile_is_downloaded_under_the_lease_and_applied(build_supervisor, fakes) -> None:
    hub = fakes.FakeHub(CAND, profile=REPLACE_PROFILE)
    assert "steps" not in json.dumps(hub.snapshot)
    supervisor = build_supervisor(hub, capabilities=(CAND,))

    assert supervisor.run(once=True) == 0

    ((_, _, headers, _),) = hub.paths("/postprocess")
    assert headers["X-Resona-Lease"] == "lease-abc"
    ((_, _, _, body),) = hub.paths("/result")
    assert JobResult.model_validate_json(body).markdown == "hallo Welt"
    assert hub.paths("/fail") == []


def test_postprocess_profile_hash_mismatch_fails_as_invalid_snapshot(build_supervisor, fakes) -> None:
    hub = fakes.FakeHub(CAND, profile=REPLACE_PROFILE)
    hub.postprocess_document = json.dumps({**REPLACE_PROFILE, "name": "tampered"}).encode()
    supervisor = build_supervisor(hub, capabilities=(CAND,))

    supervisor.run(once=True)

    ((_, _, _, body),) = hub.paths("/fail")
    failure = json.loads(body)
    assert failure["code"] == "invalid_snapshot"
    assert failure["retryable"] is False
    assert hub.paths("/result") == []


def test_text_job_runs_only_the_profile_without_the_execution_subprocess(build_supervisor, fakes) -> None:
    text = "hallo welt\n".encode()
    snapshot = {**fakes.snapshot_for(CAND, kind="text"), "candidate": None, "candidates": []}
    hub = fakes.FakeHub(CAND, kind="text", job_input=text, snapshot=snapshot, profile=REPLACE_PROFILE)
    hub.approved_capabilities = []
    # A worker without any tested tuple still takes text jobs.
    supervisor = build_supervisor(hub, capabilities=())

    assert supervisor.run(once=True) == 0

    ((_, _, _, claim_body),) = hub.paths("/claim")
    assert json.loads(claim_body) == {"capabilities": [], "text_jobs": True}
    assert hub.heartbeat_bodies[0]["text_jobs"] is True
    ((_, _, _, body),) = hub.paths("/result")
    result = JobResult.model_validate_json(body)
    assert (result.kind, result.language) == ("text", "de")
    assert (result.raw_text, result.raw_markdown, result.markdown) == ("hallo welt\n", "hallo welt\n", "hallo Welt\n")
    # No engine ran: the stub executor would have recorded an asr stage.
    assert result.segments == [] and result.provenance.stages == []
    assert result.provenance.input_sha256 == hashlib.sha256(text).hexdigest()
    assert hub.paths("/fail") == []


def test_text_job_input_that_is_not_utf8_fails_terminally(build_supervisor, fakes) -> None:
    snapshot = {**fakes.snapshot_for(CAND, kind="text"), "candidate": None, "candidates": []}
    hub = fakes.FakeHub(CAND, kind="text", job_input=b"\xc3\x28", snapshot=snapshot, profile=REPLACE_PROFILE)
    supervisor = build_supervisor(hub, capabilities=())

    supervisor.run(once=True)

    ((_, _, _, body),) = hub.paths("/fail")
    assert (json.loads(body)["code"], json.loads(body)["retryable"]) == ("unsupported_input", False)
    assert hub.paths("/result") == []


def test_document_input_is_named_by_its_magic_bytes_for_the_converter(build_supervisor, fakes) -> None:
    doc = Candidate(engine="docling", model="docling-standard", accelerator="metal", ocr_engine="ocrmac")
    hub = fakes.FakeHub(doc, kind="document", job_input=b"%PDF-1.7\nsynthetic")
    supervisor = build_supervisor(hub, capabilities=(doc,))

    supervisor.run(once=True)

    ((_, _, _, body),) = hub.paths("/result")
    assert JobResult.model_validate_json(body).raw_text == "suffix=.pdf"


def test_document_input_of_unknown_type_fails_terminally(build_supervisor, fakes) -> None:
    doc = Candidate(engine="docling", model="docling-standard", accelerator="metal", ocr_engine="ocrmac")
    hub = fakes.FakeHub(doc, kind="document", job_input=b"PK\x03\x04 not a pdf")
    supervisor = build_supervisor(hub, capabilities=(doc,))

    supervisor.run(once=True)

    ((_, _, _, body),) = hub.paths("/fail")
    failure = json.loads(body)
    assert (failure["code"], failure["retryable"]) == ("unsupported_input", False)
    assert hub.paths("/result") == []


def test_exited_executor_group_answering_eperm_does_not_crash_the_worker(build_supervisor, fakes, monkeypatch) -> None:
    """macOS: killpg on a reaped group that still holds zombies raises EPERM. The job's
    result must still be uploaded instead of the supervisor dying mid-job."""
    import os

    from resona_worker import supervisor as supervisor_module

    calls: list[int] = []

    def killpg(pid: int, sig: int) -> None:
        calls.append(pid)
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(supervisor_module.os, "killpg", killpg)
    hub = fakes.FakeHub(CAND)
    supervisor = build_supervisor(hub, capabilities=(CAND,))

    assert supervisor.run(once=True) == 0

    assert calls and all(pid != os.getpid() for pid in calls)
    assert len(hub.paths("/result")) == 1
    assert hub.paths("/fail") == []
