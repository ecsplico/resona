import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from resona_asr_core.protocol import Transcriber
from resona_engine_lightning_mlx.transcriber import (
    LightningMLXTranscriber,
    _models_dir,
    _write_temp_wav,
)


@pytest.fixture(autouse=True)
def _models_dir_tmp(monkeypatch, tmp_path):
    """Keep model downloads out of ~/.lmstudio during tests."""
    monkeypatch.setenv("LIGHTNING_MLX_MODELS_DIR", str(tmp_path))


def _install_fake_lightning(monkeypatch, transcribe_return, model_name="large-v3"):
    """Fake the lightning_whisper_mlx package + its transcribe submodule."""
    instance = MagicMock()
    instance.name = model_name
    cls = MagicMock(return_value=instance)

    module = types.ModuleType("lightning_whisper_mlx")
    module.LightningWhisperMLX = cls
    submod = types.ModuleType("lightning_whisper_mlx.transcribe")
    transcribe_audio = MagicMock(return_value=transcribe_return)
    submod.transcribe_audio = transcribe_audio
    module.transcribe = submod

    monkeypatch.setitem(sys.modules, "lightning_whisper_mlx", module)
    monkeypatch.setitem(sys.modules, "lightning_whisper_mlx.transcribe", submod)
    return cls, instance, transcribe_audio


def test_satisfies_protocol(monkeypatch):
    _install_fake_lightning(monkeypatch, {"text": "", "segments": []})
    t = LightningMLXTranscriber(modelname="large-v3")
    assert isinstance(t, Transcriber)


def test_models_dir_defaults_to_lmstudio(monkeypatch):
    monkeypatch.delenv("LIGHTNING_MLX_MODELS_DIR", raising=False)
    assert _models_dir() == Path.home() / ".lmstudio" / "models"


def test_models_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("LIGHTNING_MLX_MODELS_DIR", str(tmp_path / "custom"))
    assert _models_dir() == tmp_path / "custom"


def test_model_downloaded_under_models_dir(monkeypatch, tmp_path):
    """Construction runs with CWD set to the models dir and records an absolute path."""
    _install_fake_lightning(monkeypatch, {"text": "", "segments": []})
    t = LightningMLXTranscriber(modelname="large-v3")
    assert t._model_path == str((tmp_path / "mlx_models" / "large-v3").resolve())


def test_write_temp_wav_roundtrip():
    import wave

    audio = np.zeros(1600, dtype=np.float32)
    path = _write_temp_wav(audio)
    try:
        with wave.open(path, "rb") as w:
            assert w.getnchannels() == 1
            assert w.getframerate() == 16000
            assert w.getnframes() == 1600
    finally:
        import os

        os.unlink(path)


def test_transcribe_parses_triples(monkeypatch):
    _install_fake_lightning(
        monkeypatch,
        {"text": "hallo welt", "segments": [[0.0, 1.0, "hallo"], [1.0, 2.0, " welt"]]},
    )
    t = LightningMLXTranscriber(modelname="large-v3")
    result = t.transcribe(np.zeros(16000, dtype=np.float32), language="de")

    assert result["text"] == "hallo welt"
    assert result["language"] == "de"
    assert result["segments"][0] == {"start": 0.0, "end": 1.0, "text": "hallo"}


def test_transcribe_uses_absolute_model_path_and_cleans_up(monkeypatch):
    _, _, transcribe_audio = _install_fake_lightning(monkeypatch, {"text": "x", "segments": []})
    t = LightningMLXTranscriber(modelname="large-v3")
    t.transcribe(np.zeros(16000), language="en", initial_prompt="ignored")

    args, kwargs = transcribe_audio.call_args
    assert kwargs["language"] == "en"
    assert kwargs["path_or_hf_repo"] == t._model_path  # absolute, not ./mlx_models
    # first positional arg is the temp wav path, cleaned up afterwards
    assert not Path(args[0]).exists()
