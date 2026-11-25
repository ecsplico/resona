"""Job context: the text around the insertion point reaches every llm/extract call."""

from types import SimpleNamespace

import pytest

import resona_postprocess.llm as llm_mod
from resona_job_core import JobContext
from resona_job_core.policy import Endpoint, EndpointKind, ExecutionPolicy
from resona_postprocess.llm import _messages
from resona_postprocess.pipeline import build_pipeline
from resona_postprocess.profile import Profile, ProfileError

LOCAL_LLM = Endpoint(id="local-llm", kind=EndpointKind.LOCAL_LLM, model="openai/qwen", url="http://127.0.0.1:9292/v1")
POLICY = ExecutionPolicy(private=True, cloud_enabled=False, approved_endpoint_ids=("local-llm",), endpoints=(LOCAL_LLM,))


def _profile(**extra) -> Profile:
    return Profile.from_dict({"name": "cleanup", "steps": [
        {"type": "llm", "name": "cleanup", "prompt": "Clean up the dictation.", "endpoint": "local-llm"},
        {"type": "extract", "name": "title", "prompt": "Return a JSON title.", "endpoint": "local-llm"},
    ], **extra})


def test_messages_without_context_or_instructions_send_the_bare_text() -> None:
    assert _messages("P", "hello") == [{"role": "system", "content": "P"}, {"role": "user", "content": "hello"}]


def test_context_follows_the_text_and_adds_the_insert_placement_rule() -> None:
    system, user = _messages("P", "neuer Satz", context={"before": "Davor.", "after": "Danach."})
    assert user["content"] == (
        "<text>\nneuer Satz\n</text>\n\n"
        "<context_before>\nDavor.\n</context_before>\n\n"
        "<context_after>\nDanach.\n</context_after>"
    )
    assert system["content"].startswith("P\n\nThe text will be inserted between context_before and context_after.")
    assert "never copy or repeat the context" in system["content"]


def test_selection_context_uses_the_replace_rule_after_instructions() -> None:
    system, user = _messages("P", "neu", "Keep names.", {"before": "A", "selection": "alt", "after": "B"})
    assert user["content"] == (
        "<instructions>\nKeep names.\n</instructions>\n\n<text>\nneu\n</text>\n\n"
        "<context_before>\nA\n</context_before>\n\n"
        "<context_selection>\nalt\n</context_selection>\n\n"
        "<context_after>\nB\n</context_after>"
    )
    assert "The text will replace context_selection" in system["content"]


def test_pipeline_sends_context_to_every_llm_step_and_unwraps_an_echoed_text(monkeypatch) -> None:
    calls: list[list[dict]] = []

    def completion(**kwargs):
        calls.append(kwargs["messages"])
        content = '{"title": "Befund"}' if kwargs.get("response_format") else "<text>\nSauberer Satz.\n</text>"
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None)

    monkeypatch.setattr(llm_mod, "litellm", SimpleNamespace(completion=completion))
    profile = _profile().with_context(JobContext(before="Im Befund steht:", after=""))

    result = build_pipeline(profile, policy=POLICY).run("sauberer satz")

    assert result.text == "Sauberer Satz."
    assert result.data == {"title": {"title": "Befund"}}
    assert len(calls) == 2
    for system, user in calls:
        assert "<context_before>\nIm Befund steht:\n</context_before>" in user["content"]
        assert "<context_after>" not in user["content"]
        assert "context_before" in system["content"]


def test_context_round_trips_through_the_artifact_document() -> None:
    profile = _profile().with_context({"before": "Vorher", "selection": "", "after": "Nachher"})
    assert profile.context == {"before": "Vorher", "after": "Nachher"}
    assert Profile.from_dict(profile.to_dict()).context == {"before": "Vorher", "after": "Nachher"}
    assert "context" not in _profile().to_dict()


def test_context_needs_an_llm_step() -> None:
    rules = Profile.from_dict({"name": "rules", "steps": [
        {"type": "replacements", "rules": [{"pattern": "Komma", "replacement": ","}]},
    ]})
    with pytest.raises(ProfileError):
        rules.with_context({"before": "x"})


@pytest.mark.parametrize("context", [
    {},
    {"before": "  ", "after": ""},
    {"before": "x" * 4001},
    {"cursor": "x"},
    "before",
])
def test_invalid_context_is_rejected(context) -> None:
    with pytest.raises(ProfileError):
        _profile(context=context)
