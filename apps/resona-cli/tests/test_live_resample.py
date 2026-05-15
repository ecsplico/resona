import numpy as np

from resona_cli import live_ui


def test_resample_converts_to_asr_rate():
    """A 1-second 440 Hz tone resamples to ~1 second at the ASR rate."""
    assert live_ui.MIC_SAMPLE_RATE != live_ui.ASR_SAMPLE_RATE, (
        "test assumes differing rates (the default 44100 vs 16000)"
    )
    t = np.arange(live_ui.MIC_SAMPLE_RATE, dtype=np.float32) / live_ui.MIC_SAMPLE_RATE
    one_second = np.sin(2 * np.pi * 440.0 * t).astype(np.float32)
    out = live_ui._resample_to_asr(one_second)
    assert abs(len(out) - live_ui.ASR_SAMPLE_RATE) < 100
    assert out.dtype == np.float32
    # the tone survives resampling — non-trivial signal energy preserved
    assert np.abs(out).max() > 0.5


def test_resample_is_identity_when_rates_match(monkeypatch):
    """When the rates already match, the chunk is returned unchanged."""
    monkeypatch.setattr(live_ui, "_NEEDS_RESAMPLE", False)
    audio = np.arange(1000, dtype=np.float32)
    out = live_ui._resample_to_asr(audio)
    assert out is audio
