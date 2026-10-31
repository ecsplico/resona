import pytest

from resona_job_core.policy import Endpoint, EndpointKind, ExecutionPolicy, PolicyDenied
from resona_postprocess.pipeline import (
    PostprocessPipeline,
    PostprocessResult,
    build_pipeline,
    local_policy,
)
from resona_postprocess.profile import Profile


def _profile(*steps):
    return Profile.from_dict({"name": "p", "steps": list(steps)})


def test_pipeline_runs_text_steps_in_order():
    pipe = PostprocessPipeline()
    pipe.add_text("up", str.upper)
    pipe.add_text("excl", lambda t: t + "!")
    result = pipe.run("hi")
    assert isinstance(result, PostprocessResult)
    assert result.text == "HI!"
    assert result.data == {}


def test_pipeline_extract_step_populates_data():
    pipe = PostprocessPipeline()
    pipe.add_extract("fields", lambda t: {"len": len(t)})
    result = pipe.run("abcd")
    assert result.text == "abcd"
    assert result.data == {"fields": {"len": 4}}


def test_pipeline_failing_optional_step_is_skipped():
    pipe = PostprocessPipeline()

    def boom(_):
        raise RuntimeError("llm down")

    pipe.add_text("bad", boom)
    pipe.add_text("ok", str.upper)
    result = pipe.run("hi")
    assert result.text == "HI"  # generic failure skipped, next step still ran


def test_run_propagates_policy_denied_raised_inside_a_step():
    pipe = PostprocessPipeline()

    def denied(_):
        raise PolicyDenied("polish", "cloud processing is disabled")

    pipe.add_text("polish", denied)
    pipe.add_text("after", str.upper)
    with pytest.raises(PolicyDenied):
        pipe.run("hi")


def test_private_profile_with_cloud_llm_step_is_denied_before_any_call(fake_litellm):
    profile = _profile({"type": "llm", "name": "polish", "prompt": "fix", "model": "gpt-4o-mini"})
    with pytest.raises(PolicyDenied) as exc:
        build_pipeline(profile, policy=local_policy(profile, base=ExecutionPolicy(private=True)))
    assert exc.value.code == "policy_denied"
    assert exc.value.stage == "polish"
    assert fake_litellm.calls == []


def test_non_private_local_policy_allows_cloud_llm_step(fake_litellm):
    profile = _profile({"type": "llm", "name": "polish", "prompt": "fix", "model": "gpt-4o-mini"})
    pipe = build_pipeline(profile, policy=local_policy(profile, base=ExecutionPolicy(private=False, cloud_enabled=True)))
    pipe.run("raw")
    assert fake_litellm.calls[0]["model"] == "gpt-4o-mini"
    assert fake_litellm.calls[0]["api_base"] is None


def test_loopback_legacy_ollama_step_runs_under_local_policy(fake_litellm):
    profile = _profile({"type": "extract", "name": "f", "prompt": "x", "model": "ollama/llama3"})
    result = build_pipeline(profile, policy=local_policy(profile, base=ExecutionPolicy(private=True))).run("raw")
    assert result.data == {"f": {"k": 1}}
    assert fake_litellm.calls[0]["model"] == "ollama/llama3"
    assert fake_litellm.calls[0]["api_base"] == "http://localhost:11434"


def test_endpoint_id_step_resolves_from_approved_local_endpoint(fake_litellm):
    endpoint = Endpoint(
        id="ep-ollama",
        kind=EndpointKind.LOCAL_LLM,
        model="ollama/llama3",
        url="http://localhost:11434",
    )
    policy = ExecutionPolicy(
        private=True,
        approved_endpoint_ids=("ep-ollama",),
        endpoints=(endpoint,),
    )
    profile = _profile({"type": "llm", "name": "polish", "prompt": "fix", "endpoint": "ep-ollama"})
    result = build_pipeline(profile, policy=policy).run("raw")
    assert result.text == '{"k": 1}'
    assert fake_litellm.calls[0]["model"] == "ollama/llama3"
    assert fake_litellm.calls[0]["api_base"] == "http://localhost:11434"


def test_registered_but_unapproved_endpoint_id_is_denied(fake_litellm):
    endpoint = Endpoint(
        id="ep-ollama",
        kind=EndpointKind.LOCAL_LLM,
        model="ollama/llama3",
        url="http://localhost:11434",
    )
    policy = ExecutionPolicy(private=True, approved_endpoint_ids=(), endpoints=(endpoint,))
    profile = _profile({"type": "llm", "name": "polish", "prompt": "fix", "endpoint": "ep-ollama"})
    with pytest.raises(PolicyDenied):
        build_pipeline(profile, policy=policy)
    assert fake_litellm.calls == []


def test_unknown_endpoint_id_is_denied(fake_litellm):
    profile = _profile({"type": "llm", "name": "polish", "prompt": "fix", "endpoint": "missing"})
    with pytest.raises(PolicyDenied, match="not in the policy registry"):
        build_pipeline(profile, policy=ExecutionPolicy(private=True))
    assert fake_litellm.calls == []


def test_legacy_step_absent_from_hub_policy_is_denied(fake_litellm):
    profile = _profile({"type": "llm", "name": "polish", "prompt": "fix", "model": "ollama/llama3"})
    with pytest.raises(PolicyDenied):
        build_pipeline(profile, policy=ExecutionPolicy(private=True))
    assert fake_litellm.calls == []


def test_legacy_step_without_any_model_is_denied(monkeypatch):
    monkeypatch.delenv("RESONA_LLM_MODEL", raising=False)
    profile = _profile({"type": "llm", "name": "polish", "prompt": "fix"})
    with pytest.raises(PolicyDenied, match="no model configured"):
        local_policy(profile, base=ExecutionPolicy(private=True))


def test_build_pipeline_replacements_and_extract(fake_litellm):
    profile = _profile(
        {"type": "replacements", "rules": [{"pattern": r"\bx\b", "replacement": "y"}]},
        {"type": "extract", "name": "f", "prompt": "extract", "model": "ollama/llama3"},
    )
    result = build_pipeline(profile, policy=local_policy(profile, base=ExecutionPolicy(private=True))).run("x x")
    assert result.text == "y y"
    assert result.data == {"f": {"k": 1}}


def test_build_pipeline_replacements_from_bundled_source():
    profile = _profile({"type": "replacements", "source": "default_replacements.json"})
    pipe = build_pipeline(profile, policy=local_policy(profile, base=ExecutionPolicy(private=True)))
    # 'Komma' is a default rule; pipeline must resolve the bundled source file.
    assert "," in pipe.run("a Komma b").text


def test_local_policy_keeps_org_cloud_disabled_even_when_not_private(fake_litellm):
    profile = _profile({"type": "llm", "name": "polish", "prompt": "fix", "model": "gpt-4o-mini"})
    with pytest.raises(PolicyDenied, match="cloud processing is disabled"):
        build_pipeline(profile, policy=local_policy(profile, base=ExecutionPolicy(private=False)))
    assert fake_litellm.calls == []
