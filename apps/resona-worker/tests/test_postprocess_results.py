"""Postprocess output reaches the uploaded result: extract data, skipped steps, endpoint provenance, audio paragraphs."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from resona_job_core import Candidate, JobResult
from resona_postprocess import llm as llm_module

from resona_worker import execute

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


# Runs the real executor with a stub engine, so the supervisor postprocesses the Markdown
# that ``execute.audio_markdown`` built from the segments.
SEGMENTED_EXECUTOR = r'''
import sys
from resona_worker import execute

class Engine:
    def __init__(self, device, modelname):
        pass

    def transcribe(self, audio, **kwargs):
        return {"text": "Hallo welt. Tschüss welt.", "language": "de", "segments": [
            {"start": 0.0, "end": 1.0, "text": "Hallo welt."},
            {"start": 3.0, "end": 4.0, "text": "Tschüss welt."},
        ]}

execute._load_engine = lambda name: Engine
sys.exit(execute.main(sys.argv[1:]))
'''


def test_profile_output_replaces_the_paragraphed_audio_markdown(fakes, build_supervisor, executor_script) -> None:
    executor_script.write_text(SEGMENTED_EXECUTOR, encoding="utf-8")
    wav = Path(execute.__file__).parent / "fixtures" / "de_short.wav"
    profile = _profile({"type": "replacements", "rules": [{"pattern": "welt", "replacement": "Welt"}]})
    hub = fakes.FakeHub(CAND, job_input=wav.read_bytes(), profile=profile)

    assert build_supervisor(hub, capabilities=(CAND,)).run(once=True) == 0

    ((_, _, _, body),) = hub.paths("/result")
    result = JobResult.model_validate_json(body)
    assert result.markdown == "Hallo Welt.\n\nTschüss Welt."
    assert result.raw_markdown == "Hallo welt.\n\nTschüss welt."
    assert result.raw_text == "Hallo welt. Tschüss welt."
