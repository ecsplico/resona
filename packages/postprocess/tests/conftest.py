from types import SimpleNamespace

import pytest

import resona_postprocess.llm as llm_mod


class FakeLiteLLM:
    """Stand-in for the litellm module. Records every completion call.

    ``script`` is consumed in order; the last entry repeats. An exception entry
    is raised instead of returned.
    """

    def __init__(self, *script):
        self.script = list(script)
        self.calls: list[dict] = []

    def completion(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, BaseException):
            raise item
        message = SimpleNamespace(content=item)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


@pytest.fixture
def fake_litellm(monkeypatch):
    fake = FakeLiteLLM('{"k": 1}')
    monkeypatch.setattr(llm_mod, "litellm", fake)
    return fake
