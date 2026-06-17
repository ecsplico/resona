import sys
from unittest.mock import MagicMock

import numpy as np

from resona_asr_core.protocol import Transcriber
from resona_engine_mlx_whisper.transcriber import (
    MLXWhisperTranscriber,
    _resolve_repo,
)


def _install_fake_mlx(monkeypatch, transcribe_return):
    fake = MagicMock()
    fake.transcribe.return_value = transcribe_return
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake)
    return fake


def test_satisfies_protocol():
    t = MLXWhisperTranscriber(modelname="tiny")
    assert isinstance(t, Transcriber)


def test_alias_resolves_to_repo():
    assert _resolve_repo("large-v3") == "mlx-community/whisper-large-v3-mlx"
    # an explicit repo passes through untouched
    assert _resolve_repo("mlx-community/whisper-tiny") == "mlx-community/whisper-tiny"


def test_transcribe_returns_expected_keys(monkeypatch):
    _install_fake_mlx(
        monkeypatch,
        {"text": "hallo welt", "language": "de", "segments": [{"text": "hallo welt"}]},
    )
    t = MLXWhisperTranscriber(modelname="large-v3")
    result = t.transcribe(np.zeros(16000, dtype=np.float32), language="de")

    assert result["text"] == "hallo welt"
    assert result["language"] == "de"
    assert result["segments"] == [{"text": "hallo welt"}]


def test_initial_prompt_and_repo_passed_through(monkeypatch):
    fake = _install_fake_mlx(monkeypatch, {"text": "", "language": "de", "segments": []})
    t = MLXWhisperTranscriber(modelname="large-v3")
    t.transcribe(np.zeros(16000), initial_prompt="Befund")

    _, kwargs = fake.transcribe.call_args
    assert kwargs["initial_prompt"] == "Befund"
    assert kwargs["path_or_hf_repo"] == "mlx-community/whisper-large-v3-mlx"
