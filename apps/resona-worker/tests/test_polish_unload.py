"""Document polish on the worker: chunk warnings reach the result, hard failure, model unload."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
from resona_job_core import Candidate, JobResult
from resona_postprocess import llm as llm_mod

CAND = Candidate(engine="faster-whisper", model="tiny", model_revision="rev1", accelerator="cpu")
UNLOAD_URL = "http://127.0.0.1:9292/api/models/unload/qwen3.6-27b"


def _policy(unload_url: str | None = UNLOAD_URL) -> dict[str, Any]:
    endpoint = {
        "id": "gpu-llama",
        "kind": "local_llm",
        "model": "openai/qwen3.6-27b",
        "url": "http://127.0.0.1:9292/v1",
        "worker_id": "w1",
    }
    if unload_url is not None:
        endpoint["unload_url"] = unload_url
    return {"private": True, "cloud_enabled": False, "approved_endpoint_ids": ["gpu-llama"], "endpoints": [endpoint]}


POLISH = {"type": "llm", "name": "ocr-polish", "prompt": "POLISH", "endpoint": "gpu-llama",
          "chunk_chars": 1000, "max_attempts": 2, "max_length_deviation": 0.3}
SECOND = {"type": "llm", "name": "second", "prompt": "SECOND", "endpoint": "gpu-llama"}


class Recorder:
    """Shared event log for the fake LLM and the fake model server's unload route."""

    def __init__(self, reply) -> None:
        self.reply = reply
        self.events: list[str] = []

    def completion(self, **kwargs: Any) -> SimpleNamespace:
        self.events.append("llm:" + kwargs["messages"][0]["content"])
        content = self.reply(kwargs["messages"][1]["content"])
        if isinstance(content, BaseException):
            raise content
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None)

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.events.append(f"{request.method} {request.url}")
            return httpx.Response(200, json={"msg": "ok"})

        return httpx.MockTransport(handle)


def _run(fakes, build_supervisor, monkeypatch, recorder: Recorder, *steps: dict, policy=None):
    monkeypatch.setattr(llm_mod, "litellm", recorder)
    snapshot = fakes.snapshot_for(CAND, policy=policy or _policy())
    hub = fakes.FakeHub(CAND, snapshot=snapshot, profile={"name": "document-polish-de", "steps": list(steps)})
    supervisor = build_supervisor(hub, capabilities=(CAND,), endpoint_transport=recorder.transport())
    assert supervisor.run(once=True) == 0
    return hub


def test_model_is_unloaded_once_after_the_last_stage_on_its_endpoint(fakes, build_supervisor, monkeypatch) -> None:
    recorder = Recorder(lambda chunk: chunk)

    hub = _run(fakes, build_supervisor, monkeypatch, recorder, POLISH, SECOND)

    assert recorder.events == ["llm:POLISH", "llm:SECOND", f"POST {UNLOAD_URL}"]
    assert hub.paths("/result")


def test_rejected_chunk_is_a_result_warning_with_the_raw_text_kept(fakes, build_supervisor, monkeypatch) -> None:
    recorder = Recorder(lambda chunk: "x")

    hub = _run(fakes, build_supervisor, monkeypatch, recorder, POLISH)

    ((_, _, _, body),) = hub.paths("/result")
    result = JobResult.model_validate_json(body)
    assert result.markdown == "hallo welt"
    assert result.warnings == ["polish_chunk_rejected:1:length"]


def test_failing_polish_fails_the_job_and_still_unloads(fakes, build_supervisor, monkeypatch) -> None:
    recorder = Recorder(lambda chunk: ConnectionError("refused"))

    hub = _run(fakes, build_supervisor, monkeypatch, recorder, POLISH)

    assert hub.paths("/result") == []
    ((_, _, _, body),) = hub.paths("/fail")
    failure = json.loads(body)
    assert failure["code"] == "postprocess_failed"
    assert failure["retryable"] is True
    assert "hallo" not in failure["message"]
    assert recorder.events == ["llm:POLISH", "llm:POLISH", f"POST {UNLOAD_URL}"]


def test_unreachable_unload_url_does_not_fail_the_job(fakes, build_supervisor, monkeypatch) -> None:
    recorder = Recorder(lambda chunk: chunk)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    recorder.transport = lambda: httpx.MockTransport(refuse)  # type: ignore[method-assign]

    hub = _run(fakes, build_supervisor, monkeypatch, recorder, POLISH)

    assert hub.paths("/result")
    assert hub.paths("/fail") == []


def test_endpoint_without_unload_url_is_not_unloaded(fakes, build_supervisor, monkeypatch) -> None:
    recorder = Recorder(lambda chunk: chunk)

    _run(fakes, build_supervisor, monkeypatch, recorder, POLISH, policy=_policy(unload_url=None))

    assert recorder.events == ["llm:POLISH"]


def test_snapshot_with_non_loopback_unload_url_is_refused_before_any_call(
    fakes, build_supervisor, monkeypatch
) -> None:
    recorder = Recorder(lambda chunk: chunk)

    hub = _run(fakes, build_supervisor, monkeypatch, recorder, POLISH,
               policy=_policy(unload_url="http://192.168.1.20:9292/api/models/unload"))

    ((_, _, _, body),) = hub.paths("/fail")
    assert json.loads(body)["code"] == "invalid_snapshot"
    assert recorder.events == []
