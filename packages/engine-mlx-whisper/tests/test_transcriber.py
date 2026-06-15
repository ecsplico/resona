import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

from resona_asr_core.protocol import Transcriber


@pytest.fixture
def mock_mlx(monkeypatch):
    """Inject a fake mlx_whisper module (the real one only installs on Apple Silicon)."""
    mod = MagicMock()
    monkeypatch.setitem(sys.modules, "mlx_whisper", mod)
    return mod


def test_satisfies_protocol(mock_mlx):
    from resona_engine_mlx_whisper.transcriber import MlxWhisperTranscriber
    t = MlxWhisperTranscriber(device="mps", modelname="mlx-community/whisper-tiny")
    assert isinstance(t, Transcriber)


def test_transcribe_returns_expected_keys(mock_mlx):
    mock_mlx.transcribe.return_value = {
        "text": "hallo welt",
        "language": "de",
        "segments": [{"start": 0.0, "end": 1.0, "text": "hallo welt"}],
    }
    from resona_engine_mlx_whisper.transcriber import MlxWhisperTranscriber
    t = MlxWhisperTranscriber(modelname="mlx-community/whisper-tiny")
    result = t.transcribe(np.zeros(16000, dtype=np.float32), language="de")

    assert result["text"] == "hallo welt"
    assert result["language"] == "de"
    assert len(result["segments"]) == 1
    # The configured repo is forwarded to mlx_whisper.transcribe.
    assert mock_mlx.transcribe.call_args.kwargs["path_or_hf_repo"] == "mlx-community/whisper-tiny"


def test_drops_faster_whisper_kwargs(mock_mlx):
    mock_mlx.transcribe.return_value = {"text": "", "language": "de", "segments": []}
    from resona_engine_mlx_whisper.transcriber import MlxWhisperTranscriber
    t = MlxWhisperTranscriber(modelname="x")
    t.transcribe(
        np.zeros(16000, dtype=np.float32),
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 1000},
        condition_on_previous_text=False,
    )
    kw = mock_mlx.transcribe.call_args.kwargs
    assert "vad_filter" not in kw
    assert "vad_parameters" not in kw
    assert kw["condition_on_previous_text"] is False


def test_initial_prompt_passed_through(mock_mlx):
    mock_mlx.transcribe.return_value = {"text": "", "language": "de", "segments": []}
    from resona_engine_mlx_whisper.transcriber import MlxWhisperTranscriber
    t = MlxWhisperTranscriber(modelname="x")
    t.transcribe(np.zeros(16000, dtype=np.float32), initial_prompt="Befund")
    assert mock_mlx.transcribe.call_args.kwargs.get("initial_prompt") == "Befund"
