import numpy as np

from resona_cli import live_ui


def test_resample_converts_to_asr_rate():
    """A 1-second mic chunk resamples to ~1 second at the ASR rate."""
    assert live_ui.MIC_SAMPLE_RATE != live_ui.ASR_SAMPLE_RATE, (
        "test assumes differing rates (the default 44100 vs 16000)"
    )
    one_second = np.zeros(live_ui.MIC_SAMPLE_RATE, dtype=np.float32)
    out = live_ui._resample_to_asr(one_second)
    assert abs(len(out) - live_ui.ASR_SAMPLE_RATE) < 100
    assert out.dtype == np.float32


def test_resample_is_identity_when_rates_match(monkeypatch):
    """When the rates already match, the chunk is returned unchanged."""
    monkeypatch.setattr(live_ui, "_NEEDS_RESAMPLE", False)
    audio = np.arange(1000, dtype=np.float32)
    out = live_ui._resample_to_asr(audio)
    assert out is audio
