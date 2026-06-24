import sys
import types
from pathlib import Path

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


@pytest.fixture
def fake_piper(monkeypatch, tmp_path):
    """Fake the ``piper`` lib + an already-present voice file (no download)."""
    pkg = types.ModuleType("piper")
    dl = types.ModuleType("piper.download_voices")

    class _Chunk:
        def __init__(self, samples_int16, sample_rate):
            self.audio_int16_bytes = samples_int16.tobytes()
            self.sample_rate = sample_rate
            self.sample_width = 2
            self.sample_channels = 1

    class SynthesisConfig:
        def __init__(self, length_scale=1.0, **kw):
            self.length_scale = length_scale

    class PiperVoice:
        loaded_from = None

        def __init__(self, path):
            self.path = path

        @classmethod
        def load(cls, path, use_cuda=False):
            cls.loaded_from = path
            return cls(path)

        def synthesize(self, text, syn_config=None):
            self.last_config = syn_config
            half = np.full(600, 100, dtype=np.int16)
            yield _Chunk(half, 22050)
            yield _Chunk(half, 22050)

    downloaded = []

    def download_voice(voice, download_dir, force_redownload=False):
        downloaded.append((voice, download_dir))
        (Path(download_dir) / f"{voice}.onnx").write_bytes(b"onnx")

    pkg.PiperVoice = PiperVoice
    pkg.SynthesisConfig = SynthesisConfig
    dl.download_voice = download_voice
    monkeypatch.setitem(sys.modules, "piper", pkg)
    monkeypatch.setitem(sys.modules, "piper.download_voices", dl)
    # Point the voices dir at a temp location so a pre-seeded .onnx skips download.
    monkeypatch.setenv("PIPER_VOICES_DIR", str(tmp_path))
    return {"voice_cls": PiperVoice, "downloaded": downloaded, "dir": tmp_path}
