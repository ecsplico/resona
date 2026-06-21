from resona_tts_local import registry


def test_multilingual_synthesize(fake_chatterbox):
    result = registry.get_engine("chatterbox").synthesize("hi", language="de")
    assert result["content_type"] == "audio/wav"
    assert result["sample_rate"] == 24000
    assert fake_chatterbox["mtl"].instance.last["language_id"] == "de"


def test_multilingual_cloning_passes_ref_audio(fake_chatterbox):
    registry.get_engine("chatterbox").synthesize(
        "hi", language="en", ref_audio="/tmp/ref.wav"
    )
    assert fake_chatterbox["mtl"].instance.last["ref"] == "/tmp/ref.wav"


def test_turbo_synthesize(fake_chatterbox):
    result = registry.get_engine("chatterbox-turbo").synthesize(
        "laugh [laugh]", ref_audio="/tmp/ref.wav"
    )
    assert result["content_type"] == "audio/wav"
    assert fake_chatterbox["turbo"].instance.last["ref"] == "/tmp/ref.wav"
