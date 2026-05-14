"""Integration test: engine-server + faster-whisper backend via entry points."""
from unittest.mock import patch, MagicMock

import numpy as np
from fastapi.testclient import TestClient

from resona_asr_core.protocol import TranscriptionResult


def _fake_entry_point(**kwargs):
    class MockTranscriber:
        def __init__(self, device="cpu", modelname=None):
            pass

        def transcribe(self, audio, *, language="de", task="transcribe",
                       initial_prompt=None, word_timestamps=False,
                       vad_filter=False, **kwargs):
            return TranscriptionResult(
                text="integration test",
                language=language,
                segments=[],
            )

    ep = MagicMock()
    ep.name = "faster-whisper"
    ep.load.return_value = MockTranscriber
    return [ep]


@patch("resona_asr_core.registry.entry_points", side_effect=_fake_entry_point)
@patch("resona_asr_core.registry.config", return_value="faster-whisper")
@patch("resona_engine_server.auth.config", return_value=None)
def test_full_stack_transcribe(mock_auth_config, mock_reg_config, mock_eps):
    from resona_asr_core.registry import reset
    reset()

    from resona_engine_server.app import app
    client = TestClient(app)

    with patch("resona_engine_server.app.load_audio", return_value=np.zeros(16000)):
        resp = client.post(
            "/transcribe",
            files={"audio_file": ("test.wav", b"\x00" * 100, "audio/wav")},
            data={"language": "en"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "integration test"
    assert "md" not in body

    reset()
