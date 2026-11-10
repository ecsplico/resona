"""Bundled title-de profile: loads, validates, and lands the title at structured["title"]["title"]."""

from importlib import resources
from types import SimpleNamespace

import resona_postprocess.llm as llm_mod
from resona_job_core.policy import Endpoint, EndpointKind, ExecutionPolicy
from resona_postprocess.pipeline import build_pipeline, endpoint_ids
from resona_postprocess.profile import resolve_profile

LOCAL_LLM = Endpoint(id="local-llm", kind=EndpointKind.LOCAL_LLM, model="openai/qwen3-4b", url="http://127.0.0.1:9292/v1")
POLICY = ExecutionPolicy(private=True, cloud_enabled=False, approved_endpoint_ids=("local-llm",), endpoints=(LOCAL_LLM,))


def _bundled_path():
    return resources.files("resona_postprocess").joinpath("profiles/title-de.json")


def test_title_profile_resolves_and_validates(tmp_path) -> None:
    profile = resolve_profile(str(_bundled_path()), tmp_path)
    assert profile.name == "title-de"
    assert endpoint_ids(profile) == ["local-llm"]
    [step] = profile.steps
    assert step["type"] == "extract"
    assert step["name"] == "title"
    assert step["temperature"] == 0


def test_title_lands_under_the_step_name(monkeypatch, tmp_path) -> None:
    calls: list[dict] = []

    def completion(**kwargs):
        calls.append(kwargs)
        content = '{"title": "Befund Kontrolluntersuchung Knie"}'
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None)

    monkeypatch.setattr(llm_mod, "litellm", SimpleNamespace(completion=completion))
    profile = resolve_profile(str(_bundled_path()), tmp_path)
    text = "Kapitel Befund. Kontrolle des rechten Knies, keine Schwellung."

    result = build_pipeline(profile, policy=POLICY).run(text)

    assert result.text == text
    assert result.data == {"title": {"title": "Befund Kontrolluntersuchung Knie"}}
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert calls[0]["temperature"] == 0
    assert calls[0]["timeout"] == 30
