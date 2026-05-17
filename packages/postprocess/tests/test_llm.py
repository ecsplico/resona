import pytest
import resona_postprocess.llm as llm_mod
from resona_postprocess.llm import llm_transform, LLMUnavailableError


class _Msg:
    def __init__(self, content): self.message = type("M", (), {"content": content})


class _Resp:
    def __init__(self, content): self.choices = [_Msg(content)]
    usage = None


def test_llm_transform_calls_model(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return _Resp("FORMATTED")

    monkeypatch.setattr(llm_mod, "litellm", type("L", (), {"completion": staticmethod(fake_completion)}))
    out = llm_transform("raw", prompt="format", model="gpt-x", temperature=0.3)
    assert out == "FORMATTED"
    assert captured["model"] == "gpt-x"
    assert captured["temperature"] == 0.3
    assert captured["messages"][0]["content"] == "format"
    assert captured["messages"][1]["content"] == "raw"


def test_llm_transform_raises_when_litellm_missing(monkeypatch):
    monkeypatch.setattr(llm_mod, "litellm", None)
    with pytest.raises(LLMUnavailableError):
        llm_transform("raw", prompt="format")


def test_llm_transform_retries_once(monkeypatch):
    calls = {"n": 0}

    def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return _Resp("OK")

    monkeypatch.setattr(llm_mod, "litellm", type("L", (), {"completion": staticmethod(flaky)}))
    assert llm_transform("raw", prompt="p") == "OK"
    assert calls["n"] == 2
