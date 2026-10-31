"""Shared fakes: a recording stand-in for ResonaClient (no hub, no network)."""
import json
from typing import Any, Optional

import pytest
from resona_client.client import JobRef, JobView, SettingsDoc, Upload
from resona_job_core import JobResult, Provenance


class FakeHub:
    """State shared by every FakeClient a test's factory hands out."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.tokens: list[Optional[str]] = []
        self.result = JobResult(kind="audio", raw_text="raw", markdown="# x\n", provenance=Provenance())
        self.error: Optional[Exception] = None

    def client(self, token: Optional[str] = None) -> "FakeClient":
        self.tokens.append(token)
        return FakeClient(self)


class FakeClient:
    def __init__(self, hub: FakeHub) -> None:
        self.hub = hub

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def _record(self, name: str, arg: Any) -> None:
        self.hub.calls.append((name, arg))
        if self.hub.error is not None:
            raise self.hub.error

    def upload(self, path):
        self._record("upload", path)
        return Upload(upload_id="upl_" + "a" * 24, sha256="0" * 64, size=1)

    def upload_bytes(self, filename: str, data: bytes):
        self._record("upload_bytes", (filename, len(data)))
        return Upload(upload_id="upl_" + "b" * 24, sha256="1" * 64, size=len(data))

    def submit_upload(self, upload_id: str, **kwargs: Any):
        self._record("submit_upload", (upload_id, kwargs))
        return JobRef(job_id=7, status="pending")

    def get_job(self, job_id: int):
        self._record("get_job", job_id)
        return JobView.from_dict({"job_id": job_id, "kind": "audio", "status": "completed",
                                  "private": True, "result_available": True})

    def cancel_job(self, job_id: int):
        self._record("cancel_job", job_id)
        return JobView.from_dict({"job_id": job_id, "kind": "audio", "status": "cancelled", "private": True})

    def get_result(self, job_id: int):
        self._record("get_result", job_id)
        return self.hub.result

    def get_settings(self):
        self._record("get_settings", None)
        return SettingsDoc(organization_id="org-a", revision=3, etag='"3"',
                           settings={"default_execution_profile": "private-auto",
                                     "execution_profiles": {"cpu-private": {"audio": [], "document": []}}})

    def list_engines(self):
        self._record("list_engines", None)
        return {"engines": [{"name": "faster-whisper"}], "default": "faster-whisper"}


@pytest.fixture
def hub() -> FakeHub:
    return FakeHub()


def _payload(result) -> dict:
    """The JSON object a successful tool call returned."""
    assert not result.is_error, result.content
    if result.structured_content is not None:
        data = result.structured_content
        return data["result"] if set(data) == {"result"} else data
    return json.loads(result.content[0].text)


def _error_text(result) -> str:
    assert result.is_error, result.content
    return result.content[0].text


@pytest.fixture
def payload():
    return _payload


@pytest.fixture
def error_text():
    return _error_text
