# packages/engine-server/tests/test_protocol.py
import numpy as np
from resona_asr_core.protocol import Transcriber, TranscriptionResult


class _DummyTranscriber:
    def transcribe(self, audio: np.ndarray, *, language: str = "de", task: str = "transcribe",
                   initial_prompt: str | None = None, word_timestamps: bool = False,
                   vad_filter: bool = False, **kwargs) -> TranscriptionResult:
        return TranscriptionResult(text="hello", language="en", segments=[])


class _BadTranscriber:
    pass


def test_dummy_satisfies_protocol():
    t = _DummyTranscriber()
    assert isinstance(t, Transcriber)


def test_bad_transcriber_fails_protocol():
    t = _BadTranscriber()
    assert not isinstance(t, Transcriber)


def test_transcription_result_is_typed_dict():
    r = TranscriptionResult(text="hi", language="de", segments=[])
    assert r["text"] == "hi"
    assert r["language"] == "de"
    assert r["segments"] == []
