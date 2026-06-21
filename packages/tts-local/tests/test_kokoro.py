from resona_tts_local import registry


def test_synthesize_returns_wav(fake_kokoro):
    result = registry.get_engine("kokoro").synthesize("hallo", language="de")
    assert result["content_type"] == "audio/wav"
    assert result["sample_rate"] == 24000
    assert result["audio"][:4] == b"RIFF"


def test_synthesize_array_concatenates_chunks(fake_kokoro):
    samples, sr = registry.get_engine("kokoro").synthesize_array("hi")
    assert sr == 24000
    assert len(samples) == 2400  # two 1200-sample chunks


def test_language_maps_to_pipeline(fake_kokoro):
    engine = registry.get_engine("kokoro")
    engine.synthesize("x", language="ja")
    assert "j" in engine._pipelines  # ja -> 'j'


def test_unknown_language_falls_back_to_english(fake_kokoro):
    engine = registry.get_engine("kokoro")
    engine.synthesize("x", language="xx")
    assert "a" in engine._pipelines
