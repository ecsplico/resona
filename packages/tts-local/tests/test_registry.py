import pytest

from resona_tts_local import registry
from resona_tts_local.errors import EngineUnavailableError, UnknownEngineError


def test_engines_listed():
    names = registry.installed_engines()
    for expected in ("kokoro", "chatterbox", "chatterbox-turbo", "qwen", "qwen-custom-voice"):
        assert expected in names


def test_recommended_is_kokoro():
    assert registry.recommended_engine() == "kokoro"


def test_every_engine_has_info():
    for name in registry.ENGINES:
        info = registry.ENGINE_INFO[name]
        assert info["languages"]
        assert "cloning" in info


def test_unknown_engine_raises():
    with pytest.raises(UnknownEngineError):
        registry.get_engine("does-not-exist")


def test_missing_native_lib_raises_unavailable():
    # chatterbox-tts is not installed in the dev env → clear, actionable error.
    with pytest.raises(EngineUnavailableError):
        registry.get_engine("chatterbox").synthesize("hi")


def test_get_engine_memoizes(fake_kokoro):
    a = registry.get_engine("kokoro")
    b = registry.get_engine("kokoro")
    assert a is b
