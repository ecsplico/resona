import pytest

import resona_postprocess.llm as llm_mod
from resona_job_core.policy import Endpoint, EndpointKind, ExecutionPolicy, PolicyDenied
from resona_postprocess.llm import (
    LlmCallSettings,
    LLMUnavailableError,
    _resolve_api_base,
    legacy_endpoint_id,
    legacy_step_endpoint,
    llm_extract,
    llm_transform,
)

LOCAL = Endpoint(
    id="ep-local",
    kind=EndpointKind.LOCAL_LLM,
    model="ollama/llama3",
    url="http://localhost:11434",
)
CLOUD = Endpoint(id="ep-cloud", kind=EndpointKind.CLOUD_LLM, model="gpt-4o-mini", url=None)


def _policy(*endpoints, private=True, cloud_enabled=False, approved=None):
    ids = tuple(e.id for e in endpoints) if approved is None else approved
    return ExecutionPolicy(
        private=private,
        cloud_enabled=cloud_enabled,
        approved_endpoint_ids=ids,
        endpoints=endpoints,
    )


def test_llm_transform_calls_approved_endpoint(fake_litellm):
    fake_litellm.script = ["FORMATTED"]
    out = llm_transform(
        "raw", prompt="format", endpoint=LOCAL, policy=_policy(LOCAL), settings=LlmCallSettings(temperature=0.3),
    )
    assert out == "FORMATTED"
    call = fake_litellm.calls[0]
    assert call["model"] == "ollama/llama3"
    assert call["api_base"] == "http://localhost:11434"
    assert call["temperature"] == 0.3
    assert call["messages"][0]["content"] == "format"
    assert call["messages"][1]["content"] == "raw"


def test_llm_transform_raises_when_litellm_missing(monkeypatch):
    monkeypatch.setattr(llm_mod, "litellm", None)
    with pytest.raises(LLMUnavailableError):
        llm_transform("raw", prompt="format", endpoint=LOCAL, policy=_policy(LOCAL))


def test_llm_transform_retries_once(fake_litellm):
    fake_litellm.script = [RuntimeError("transient"), "OK"]
    assert llm_transform("raw", prompt="p", endpoint=LOCAL, policy=_policy(LOCAL)) == "OK"
    assert len(fake_litellm.calls) == 2


def test_llm_transform_unapproved_endpoint_never_reaches_litellm(fake_litellm):
    policy = _policy(LOCAL, approved=())
    with pytest.raises(PolicyDenied):
        llm_transform("raw", prompt="p", endpoint=LOCAL, policy=policy)
    assert fake_litellm.calls == []


def test_llm_transform_private_cloud_endpoint_never_reaches_litellm(fake_litellm):
    policy = _policy(CLOUD, private=True, cloud_enabled=True)
    with pytest.raises(PolicyDenied):
        llm_transform("raw", prompt="p", endpoint=CLOUD, policy=policy)
    assert fake_litellm.calls == []


def test_llm_transform_endpoint_differing_from_registry_is_denied(fake_litellm):
    tampered = LOCAL.model_copy(update={"url": "http://attacker.example"})
    with pytest.raises(PolicyDenied):
        llm_transform("raw", prompt="p", endpoint=tampered, policy=_policy(LOCAL))
    assert fake_litellm.calls == []


def test_llm_extract_parses_json(fake_litellm):
    fake_litellm.script = ['{"diagnose": "x"}']
    out = llm_extract("raw", prompt="extract", endpoint=LOCAL, policy=_policy(LOCAL))
    assert out == {"diagnose": "x"}


def test_llm_extract_malformed_json_keeps_raw(fake_litellm):
    fake_litellm.script = ["not json"]
    out = llm_extract("raw", prompt="extract", endpoint=LOCAL, policy=_policy(LOCAL))
    assert out == {"_raw": "not json"}


def test_llm_extract_unapproved_endpoint_never_reaches_litellm(fake_litellm):
    with pytest.raises(PolicyDenied):
        llm_extract("raw", prompt="extract", endpoint=LOCAL, policy=_policy(LOCAL, approved=()))
    assert fake_litellm.calls == []


def test_legacy_ollama_step_is_local_with_default_base(monkeypatch):
    monkeypatch.delenv("RESONA_LLM_API_BASE", raising=False)
    step = {"type": "llm", "model": "ollama/llama3", "prompt": "p"}
    ep = legacy_step_endpoint(step)
    assert ep.kind is EndpointKind.LOCAL_LLM
    assert ep.url == "http://localhost:11434"
    assert ep.model == "ollama/llama3"
    assert ep.id == legacy_endpoint_id(step)


def test_legacy_hosted_model_step_is_cloud(monkeypatch):
    monkeypatch.delenv("RESONA_LLM_API_BASE", raising=False)
    ep = legacy_step_endpoint({"type": "llm", "model": "gpt-4o-mini", "prompt": "p"})
    assert ep.kind is EndpointKind.CLOUD_LLM
    assert ep.url is None


def test_legacy_step_without_model_is_denied(monkeypatch):
    monkeypatch.delenv("RESONA_LLM_MODEL", raising=False)
    with pytest.raises(PolicyDenied, match="no model configured"):
        legacy_step_endpoint({"type": "llm", "prompt": "p"})


def test_legacy_step_uses_explicit_env_model(monkeypatch):
    monkeypatch.setenv("RESONA_LLM_MODEL", "ollama/qwen")
    monkeypatch.delenv("RESONA_LLM_API_BASE", raising=False)
    ep = legacy_step_endpoint({"type": "llm", "prompt": "p"})
    assert ep.model == "ollama/qwen"
    assert ep.kind is EndpointKind.LOCAL_LLM


def test_resolve_api_base_explicit_wins(monkeypatch):
    monkeypatch.setenv("RESONA_LLM_API_BASE", "http://env")
    assert _resolve_api_base("lm_studio/x", "http://explicit") == "http://explicit"


def test_resolve_api_base_env(monkeypatch):
    monkeypatch.setenv("RESONA_LLM_API_BASE", "http://ollama:11434")
    assert _resolve_api_base("ollama/llama3", None) == "http://ollama:11434"


def test_resolve_api_base_lm_studio_default(monkeypatch):
    monkeypatch.delenv("RESONA_LLM_API_BASE", raising=False)
    assert _resolve_api_base("lm_studio/qwen2.5", None) == "http://localhost:1234/v1"


def test_resolve_api_base_mlx_default(monkeypatch):
    monkeypatch.delenv("RESONA_LLM_API_BASE", raising=False)
    assert _resolve_api_base("mlx/Qwen2.5-7B", None) == "http://localhost:10240/v1"


def test_resolve_api_base_cloud_is_none(monkeypatch):
    monkeypatch.delenv("RESONA_LLM_API_BASE", raising=False)
    assert _resolve_api_base("gpt-4o-mini", None) is None
    assert _resolve_api_base("ollama/llama3", None) is None
