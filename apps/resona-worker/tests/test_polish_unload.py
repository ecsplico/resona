"""Document polish on the worker: chunk warnings reach the result, raw Markdown kept, hard failure, model unload."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from resona_job_core import Candidate, JobResult
from resona_postprocess import llm as llm_mod

CAND = Candidate(engine="faster-whisper", model="tiny", model_revision="rev1", accelerator="cpu")
UNLOAD_URL = "http://127.0.0.1:9292/api/models/unload/qwen3.6-27b"


def _policy(unload_url: str | None = UNLOAD_URL) -> dict[str, Any]:
    endpoint = {
        "id": "local-llm",
        "kind": "local_llm",
        "model": "openai/qwen3.6-27b",
        "url": "http://127.0.0.1:9292/v1",
        "worker_id": "w1",
    }
    if unload_url is not None:
        endpoint["unload_url"] = unload_url
    return {"private": True, "cloud_enabled": False, "approved_endpoint_ids": ["local-llm"], "endpoints": [endpoint]}


POLISH = {"type": "llm", "name": "ocr-polish", "prompt": "POLISH", "endpoint": "local-llm",
          "chunk_chars": 1000, "max_attempts": 2, "max_length_deviation": 0.3}
SECOND = {"type": "llm", "name": "second", "prompt": "SECOND", "endpoint": "local-llm"}


class Recorder:
    """Shared event log for the fake LLM and the fake model server's unload route."""

    def __init__(self, reply, unload_status: int = 200) -> None:
        self.reply = reply
        self.unload_status = unload_status
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
            return httpx.Response(self.unload_status, json={"msg": "ok"})

        return httpx.MockTransport(handle)


def _run(fakes, build_supervisor, monkeypatch, recorder: Recorder, *steps: dict, policy=None, instructions=None):
    monkeypatch.setattr(llm_mod, "litellm", recorder)
    snapshot = fakes.snapshot_for(CAND, policy=policy or _policy())
    profile = {"name": "document-polish-de", "steps": list(steps)}
    if instructions is not None:
        profile["instructions"] = instructions
    hub = fakes.FakeHub(CAND, snapshot=snapshot, profile=profile)
    supervisor = build_supervisor(hub, capabilities=(CAND,), endpoint_transport=recorder.transport())
    assert supervisor.run(once=True) == 0
    return hub


def test_model_is_unloaded_once_after_the_last_stage_on_its_endpoint(fakes, build_supervisor, monkeypatch) -> None:
    recorder = Recorder(lambda chunk: chunk)

    hub = _run(fakes, build_supervisor, monkeypatch, recorder, POLISH, SECOND)

    assert recorder.events == ["llm:POLISH", "llm:SECOND", f"POST {UNLOAD_URL}"]
    assert hub.paths("/result")


def test_polished_result_keeps_the_engine_markdown_separately(fakes, build_supervisor, monkeypatch) -> None:
    recorder = Recorder(lambda chunk: chunk.replace("welt", "Welt"))

    hub = _run(fakes, build_supervisor, monkeypatch, recorder, POLISH)

    ((_, _, _, body),) = hub.paths("/result")
    result = JobResult.model_validate_json(body)
    assert result.markdown == "hallo Welt"
    assert result.raw_markdown == "hallo welt"


def test_job_instructions_reach_the_llm_step_of_a_profile_with_rules(fakes, build_supervisor, monkeypatch) -> None:
    users: list[str] = []

    def reply(user: str) -> str:
        users.append(user)
        return "<text>\nHallo Welt\n</text>"

    rules = {"type": "replacements", "rules": [{"pattern": "hallo", "replacement": "Hallo"}]}
    hub = _run(fakes, build_supervisor, monkeypatch, Recorder(reply), rules, SECOND, instructions="Namen behalten.")

    ((_, _, _, body),) = hub.paths("/result")
    result = JobResult.model_validate_json(body)
    assert users == ["<instructions>\nNamen behalten.\n</instructions>\n\n<text>\nHallo welt\n</text>"]
    assert result.markdown == "Hallo Welt"
    assert result.raw_markdown == "hallo welt"


def test_rejected_chunk_is_a_result_warning_with_the_raw_text_kept(fakes, build_supervisor, monkeypatch) -> None:
    recorder = Recorder(lambda chunk: "x")

    hub = _run(fakes, build_supervisor, monkeypatch, recorder, POLISH)

    ((_, _, _, body),) = hub.paths("/result")
    result = JobResult.model_validate_json(body)
    assert result.markdown == "hallo welt"
    assert result.raw_markdown is None
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


def test_unreachable_unload_url_is_a_warning_and_does_not_fail_the_job(
    fakes, build_supervisor, monkeypatch, caplog
) -> None:
    recorder = Recorder(lambda chunk: chunk)
    caplog.set_level(logging.INFO, logger="resona_worker.supervisor")

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    recorder.transport = lambda: httpx.MockTransport(refuse)  # type: ignore[method-assign]

    hub = _run(fakes, build_supervisor, monkeypatch, recorder, POLISH)

    assert hub.paths("/result")
    assert hub.paths("/fail") == []
    assert len(_unload_warnings(caplog)) == 1


def _unload_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record for record in caplog.records
        if record.name == "resona_worker.supervisor" and record.levelno >= logging.WARNING
        and "local-llm" in (record.args or ())
    ]


@pytest.mark.parametrize("status", [404, 405, 500])
def test_rejected_unload_is_a_warning_with_its_status(fakes, build_supervisor, monkeypatch, caplog, status) -> None:
    recorder = Recorder(lambda chunk: chunk, unload_status=status)
    caplog.set_level(logging.INFO, logger="resona_worker.supervisor")

    hub = _run(fakes, build_supervisor, monkeypatch, recorder, POLISH)

    assert hub.paths("/result")
    (warning,) = _unload_warnings(caplog)
    assert status in warning.args


def test_accepted_unload_is_not_a_warning(fakes, build_supervisor, monkeypatch, caplog) -> None:
    recorder = Recorder(lambda chunk: chunk)
    caplog.set_level(logging.INFO, logger="resona_worker.supervisor")

    _run(fakes, build_supervisor, monkeypatch, recorder, POLISH)

    assert recorder.events[-1] == f"POST {UNLOAD_URL}"
    assert _unload_warnings(caplog) == []


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
