"""Job webhook payload and the Standard Webhooks signature."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from resona_job_core.webhooks import JobEvent, secret_key, sign

FINISHED = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def test_signature_matches_the_standard_webhooks_reference_vector():
    # Reference vector of the Standard Webhooks specification libraries.
    signature = sign(
        "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw",
        "msg_p5jXN8AQM9LWM0D4loKWxJek",
        1614265330,
        b'{"test": 2432232314}',
    )
    assert signature == "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="


def test_secret_without_prefix_is_used_as_utf8_bytes():
    assert secret_key("plain-secret") == b"plain-secret"
    # whsec_ + base64("plain-secret") decodes to the same key bytes.
    assert sign("plain-secret", "m", 1, b"{}") == sign("whsec_cGxhaW4tc2VjcmV0", "m", 1, b"{}")


def test_prefixed_secret_that_is_not_base64_is_refused():
    with pytest.raises(ValueError):
        secret_key("whsec_not base64!")


def test_payload_fields_are_identifiers_enums_and_a_time_only():
    assert set(JobEvent.model_fields) == {"type", "job_id", "kind", "status", "error_code", "finished_at"}
    event = JobEvent(type="job.failed", job_id=7, kind="audio", status="failed",
                     error_code="deadline_exceeded", finished_at=FINISHED)
    assert event.model_dump(mode="json") == {
        "type": "job.failed", "job_id": 7, "kind": "audio", "status": "failed",
        "error_code": "deadline_exceeded", "finished_at": "2026-11-20T12:00:00Z",
    }


@pytest.mark.parametrize("changes", [
    {"transcript": "Hallo Welt"},  # no extra fields
    {"filename": "memo.wav"},
    {"error_code": "the patient said hello"},  # codes are identifiers, not text
    {"error_code": "Engine Error"},
    {"kind": "Hallo Welt"},
    {"status": "completed"},  # type and status must agree
])
def test_payload_refuses_anything_that_could_carry_user_content(changes):
    base = {"type": "job.failed", "job_id": 7, "kind": "audio", "status": "failed",
            "error_code": "engine_error", "finished_at": FINISHED}
    with pytest.raises(ValidationError):
        JobEvent.model_validate({**base, **changes})


def test_completed_event_has_no_error_code():
    with pytest.raises(ValidationError):
        JobEvent(type="job.completed", job_id=1, kind="text", status="completed",
                 error_code="engine_error", finished_at=FINISHED)
