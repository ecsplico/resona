import sys
import types

import numpy as np
import pytest

from resona_tts_local import registry


@pytest.fixture
def fake_qwen_mlx(monkeypatch):
    import resona_tts_local.engines.qwen as q

    monkeypatch.setattr(q, "_is_apple_silicon", lambda: True)
    pkg = types.ModuleType("mlx_audio")
    tts = types.ModuleType("mlx_audio.tts")

    class _Result:
        def __init__(self, audio):
            self.audio = audio
            self.sample_rate = 24000

    class _Model:
        def __init__(self):
            self.calls = []

        def generate(self, text, **kwargs):
            self.calls.append(kwargs)
            return [_Result(np.zeros(1200, dtype=np.float32))]

    model = _Model()
    tts.load = lambda repo: model
    monkeypatch.setitem(sys.modules, "mlx_audio", pkg)
    monkeypatch.setitem(sys.modules, "mlx_audio.tts", tts)
    return model


@pytest.fixture
def fake_qwen_pt(monkeypatch):
    import resona_tts_local.engines.qwen as q

    monkeypatch.setattr(q, "_is_apple_silicon", lambda: False)
    mod = types.ModuleType("qwen_tts")

    class _Model:
        def __init__(self):
            self.cloned = False

        def create_voice_clone_prompt(self, ref_audio, ref_text):
            self.cloned = True
            return {"ref": ref_audio}

        def generate_voice_clone(self, text=None, voice_clone_prompt=None,
                                 language=None, instruct=None):
            return [np.zeros(2400, dtype=np.float32)], 24000

    class Qwen3TTSModel:
        instance = None

        @classmethod
        def from_pretrained(cls, repo):
            cls.instance = _Model()
            return cls.instance

    mod.Qwen3TTSModel = Qwen3TTSModel
    monkeypatch.setitem(sys.modules, "qwen_tts", mod)
    return Qwen3TTSModel


def test_qwen_mlx_plain(fake_qwen_mlx):
    result = registry.get_engine("qwen").synthesize("hi", language="en")
    assert result["sample_rate"] == 24000
    assert result["audio"][:4] == b"RIFF"


def test_qwen_mlx_cloning_passes_ref(fake_qwen_mlx):
    registry.get_engine("qwen").synthesize(
        "hi", language="de", ref_audio="r.wav", ref_text="ref"
    )
    assert fake_qwen_mlx.calls[-1].get("ref_audio") == "r.wav"


def test_qwen_pytorch_clone(fake_qwen_pt):
    registry.get_engine("qwen").synthesize("hi", ref_audio="r.wav", ref_text="t")
    assert fake_qwen_pt.instance.cloned is True


def test_qwen_custom_voice_preset_and_instruct(monkeypatch):
    import resona_tts_local.engines.qwen_custom_voice as cv  # noqa: F401

    mod = types.ModuleType("qwen_tts")

    class _Model:
        def __init__(self):
            self.kwargs = None

        def generate_custom_voice(self, **kwargs):
            self.kwargs = kwargs
            return [np.zeros(2400, dtype=np.float32)], 24000

    class Qwen3TTSModel:
        instance = None

        @classmethod
        def from_pretrained(cls, repo):
            cls.instance = _Model()
            return cls.instance

    mod.Qwen3TTSModel = Qwen3TTSModel
    monkeypatch.setitem(sys.modules, "qwen_tts", mod)

    registry.get_engine("qwen-custom-voice").synthesize(
        "hi", voice="Serena", instruct="speak warmly"
    )
    assert Qwen3TTSModel.instance.kwargs["speaker"] == "Serena"
    assert Qwen3TTSModel.instance.kwargs["instruct"] == "speak warmly"
