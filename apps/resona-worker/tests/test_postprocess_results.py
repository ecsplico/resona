"""Postprocess output reaches the uploaded result: extract data, skipped steps, endpoint provenance."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from resona_job_core import Candidate, JobResult
from resona_postprocess import llm as llm_module

CAND = Candidate(engine="faster-whisper", model="tiny", model_revision="rev1", accelerator="cpu")
LOCAL_POLICY = {
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


class FakeLiteLLM:
    """Replaces litellm at the postprocess boundary; records every call."""

    def __init__(self, *, extract_reply: str, polish_reply: str, fail_extract: bool = False) -> None:
        self.extract_reply = extract_reply
        self.polish_reply = polish_reply
        self.fail_extract = fail_extract
        self.calls: list[dict[str, Any]] = []

    def completion(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        system = kwargs["messages"][0]["content"]
        if system.startswith("EXTRACT"):
            if self.fail_extract:
                raise RuntimeError("upstream refused")
            content = self.extract_reply
        else:
            content = self.polish_reply
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None)


def _profile(*steps: dict) -> dict:
    return {"name": "combined", "steps": list(steps)}


EXTRACT = {"type": "extract", "name": "fields", "prompt": "EXTRACT the fields.", "endpoint": "local-llm"}
POLISH = {"type": "llm", "name": "polish", "prompt": "POLISH the text.", "endpoint": "local-llm"}


def _run(fakes, build_supervisor, fake_llm: FakeLiteLLM, monkeypatch, profile: dict) -> JobResult:
    monkeypatch.setattr(llm_module, "litellm", fake_llm)
    snapshot = fakes.snapshot_for(CAND, policy=LOCAL_POLICY)
    hub = fakes.FakeHub(CAND, snapshot=snapshot, profile=profile)
    supervisor = build_supervisor(hub, capabilities=(CAND,))

    assert supervisor.run(once=True) == 0

    ((_, _, _, body),) = hub.paths("/result")
    return JobResult.model_validate_json(body)


def test_extract_data_is_structured_and_each_endpoint_stage_is_recorded(
    fakes, build_supervisor, monkeypatch
) -> None:
    fake_llm = FakeLiteLLM(extract_reply='{"title": "Gutachten"}', polish_reply="Poliert.")

    result = _run(fakes, build_supervisor, fake_llm, monkeypatch, _profile(EXTRACT, POLISH))

    assert result.structured == {"fields": {"title": "Gutachten"}}
    assert result.markdown == "Poliert."
    assert result.warnings == []
    llm_stages = [stage for stage in result.provenance.stages if stage.stage == "llm"]
    assert [stage.endpoint_id for stage in llm_stages] == ["local-llm", "local-llm"]
    for stage in llm_stages:
        assert (stage.engine, stage.model, stage.accelerator) == ("litellm", "qwen-local", "external")
        assert stage.seconds >= 0
    assert [stage.stage for stage in result.provenance.stages] == ["asr", "llm", "llm"]
    assert {call["api_base"] for call in fake_llm.calls} == {"http://127.0.0.1:9/v1"}


def test_skipped_step_is_a_warning_and_gets_no_provenance(fakes, build_supervisor, monkeypatch) -> None:
    fake_llm = FakeLiteLLM(extract_reply="{}", polish_reply="Poliert.", fail_extract=True)

    result = _run(fakes, build_supervisor, fake_llm, monkeypatch, _profile(EXTRACT, POLISH))

    assert result.warnings == ["postprocess_step_skipped:fields"]
    assert "fields" not in result.structured
    assert result.markdown == "Poliert."
    assert [stage.stage for stage in result.provenance.stages] == ["asr", "llm"]
    assert result.provenance.stages[1].endpoint_id == "local-llm"


def test_bookkeeping_key_never_leaks_into_structured(fakes, build_supervisor, monkeypatch) -> None:
    fake_llm = FakeLiteLLM(extract_reply='{"a": 1}', polish_reply="x", fail_extract=True)

    result = _run(fakes, build_supervisor, fake_llm, monkeypatch, _profile(EXTRACT))

    assert "_skipped" not in result.structured
    assert result.structured == {}
    assert [w for w in result.warnings if w.startswith("postprocess_step_skipped:")] == [
        "postprocess_step_skipped:fields"
    ]

