"""``context`` on submit_job/submit_upload: sent only when set, validated before any request."""
import json
from pathlib import Path

import httpx
import pytest
import respx

from resona_client.client import ResonaClient
from resona_job_core import JobContext

BASE = "http://localhost:7000"
UPLOAD_JSON = {"upload_id": "upl_1", "sha256": "ab" * 32, "size": 10}


@pytest.fixture
def client():
    c = ResonaClient(base_url=BASE, api_key="key")
    yield c
    c.close()


@pytest.fixture
def text_file(tmp_path: Path) -> Path:
    path = tmp_path / "note.md"
    path.write_text("neuer satz", encoding="utf-8")
    return path


@respx.mock
def test_context_is_sent_only_when_set(client, text_file):
    respx.post(f"{BASE}/v1/uploads").mock(return_value=httpx.Response(201, json=UPLOAD_JSON))
    jobs = respx.post(f"{BASE}/v1/jobs").mock(
        return_value=httpx.Response(202, json={"job_id": 1, "status": "pending"}))
    client.submit_job(text_file, kind="text", profile="cleanup")
    client.submit_job(text_file, kind="text", profile="cleanup",
                      context={"before": "Davor.", "selection": "", "after": "Danach."})
    client.submit_upload("upl_1", kind="text", profile="cleanup", context=JobContext(selection="alt"))
    sent = [json.loads(call.request.content) for call in jobs.calls]
    assert "context" not in sent[0]
    assert sent[1]["context"] == {"before": "Davor.", "after": "Danach."}
    assert sent[2]["context"] == {"selection": "alt"}


@respx.mock
@pytest.mark.parametrize("context", [{}, {"before": "x" * 4001}, {"cursor": "x"}])
def test_invalid_context_fails_before_any_request(client, context):
    jobs = respx.post(f"{BASE}/v1/jobs")
    with pytest.raises(ValueError):
        client.submit_upload("upl_1", kind="text", profile="cleanup", context=context)
    assert not jobs.called
