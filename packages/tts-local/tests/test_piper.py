import sys

import pytest

from resona_tts_local import registry
from resona_tts_local.errors import EngineUnavailableError


def test_synthesize_returns_wav(fake_piper):
    result = registry.get_engine("piper").synthesize("hallo", language="de")
    assert result["content_type"] == "audio/wav"
    assert result["sample_rate"] == 22050
    assert result["audio"][:4] == b"RIFF"


def test_synthesize_array_concatenates_chunks(fake_piper):
    samples, sr = registry.get_engine("piper").synthesize_array("hi", language="de")
    assert sr == 22050
    assert len(samples) == 1200  # two 600-sample chunks
    assert samples.dtype.name == "float32"


def test_language_maps_to_voice(fake_piper):
    registry.get_engine("piper").synthesize("x", language="en")
    voice, _ = fake_piper["downloaded"][-1] if fake_piper["downloaded"] else (None, None)
    # en pre-seeded? No — download happens, voice id is the english default.
    assert fake_piper["voice_cls"].loaded_from.endswith("en_US-lessac-medium.onnx")


def test_unknown_language_falls_back_to_german(fake_piper):
    registry.get_engine("piper").synthesize("x", language="xx")
    assert fake_piper["voice_cls"].loaded_from.endswith("de_DE-thorsten-medium.onnx")


def test_explicit_voice_overrides_language(fake_piper):
    registry.get_engine("piper").synthesize(
        "x", language="en", voice="de_DE-kerstin-low"
    )
    assert fake_piper["voice_cls"].loaded_from.endswith("de_DE-kerstin-low.onnx")


def test_speed_maps_to_inverse_length_scale(fake_piper):
    engine = registry.get_engine("piper")
    engine.synthesize("x", language="de", speed=2.0)
    voice = engine._voices["de_DE-thorsten-medium"]
    assert abs(voice.last_config.length_scale - 0.5) < 1e-6


def test_missing_voice_download_failure_raises(fake_piper, monkeypatch):
    # download_voice raises (offline) and no .onnx pre-exists → clear error.
    def boom(voice, download_dir, force_redownload=False):
        raise RuntimeError("offline")

    sys.modules["piper.download_voices"].download_voice = boom
    with pytest.raises(EngineUnavailableError) as exc:
        registry.get_engine("piper").synthesize("x", language="fr")
    assert "python -m piper.download_voices" in str(exc.value)


def test_missing_native_lib_raises_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "piper", None)
    with pytest.raises(EngineUnavailableError):
        registry.get_engine("piper").synthesize("hi")
