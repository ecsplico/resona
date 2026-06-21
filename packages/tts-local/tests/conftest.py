import sys
import types

import numpy as np
import pytest

from resona_tts_local import registry


@pytest.fixture(autouse=True)
def _reset_registry():
    """Each test gets fresh (un-memoized) engine instances."""
    registry.reset()
    yield
    registry.reset()


@pytest.fixture
def fake_kokoro(monkeypatch):
    mod = types.ModuleType("kokoro")

    class _Result:
        def __init__(self, audio):
            self.audio = audio

    class KModel:
        def __init__(self, repo_id=None):
            self.repo_id = repo_id

        def to(self, device):
            return self

        def eval(self):
            return self

    class KPipeline:
        def __init__(self, lang_code=None, repo_id=None, model=None):
            self.lang_code = lang_code

        def __call__(self, text, voice=None, speed=1.0):
            yield _Result(np.full(1200, 0.1, dtype=np.float32))
            yield _Result(np.full(1200, 0.2, dtype=np.float32))

    mod.KModel = KModel
    mod.KPipeline = KPipeline
    monkeypatch.setitem(sys.modules, "kokoro", mod)
    return mod


@pytest.fixture
def fake_chatterbox(monkeypatch):
    pkg = types.ModuleType("chatterbox")
    mtl = types.ModuleType("chatterbox.mtl_tts")
    turbo = types.ModuleType("chatterbox.tts_turbo")

    class _Model:
        sr = 24000

        def __init__(self):
            self.last = {}

        def generate(self, text, language_id=None, audio_prompt_path=None):
            self.last = {"language_id": language_id, "ref": audio_prompt_path}
            return np.zeros(2400, dtype=np.float32)

    class ChatterboxMultilingualTTS:
        instance = None

        @classmethod
        def from_pretrained(cls, device=None):
            cls.instance = _Model()
            return cls.instance

    class ChatterboxTurboTTS:
        instance = None

        @classmethod
        def from_pretrained(cls, device=None):
            cls.instance = _Model()
            return cls.instance

    mtl.ChatterboxMultilingualTTS = ChatterboxMultilingualTTS
    turbo.ChatterboxTurboTTS = ChatterboxTurboTTS
    monkeypatch.setitem(sys.modules, "chatterbox", pkg)
    monkeypatch.setitem(sys.modules, "chatterbox.mtl_tts", mtl)
    monkeypatch.setitem(sys.modules, "chatterbox.tts_turbo", turbo)
    return {"mtl": ChatterboxMultilingualTTS, "turbo": ChatterboxTurboTTS}
