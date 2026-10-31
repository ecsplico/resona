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
