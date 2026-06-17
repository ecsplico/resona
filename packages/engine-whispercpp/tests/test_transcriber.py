import sys
import types
from unittest.mock import MagicMock

import numpy as np

from resona_asr_core.protocol import Transcriber
from resona_engine_whispercpp.transcriber import WhisperCppTranscriber


class _Seg:
    def __init__(self, t0, t1, text):
        self.t0 = t0
        self.t1 = t1
        self.text = text


def _install_fake_pywhispercpp(monkeypatch, segments):
    model_instance = MagicMock()
    model_instance.transcribe.return_value = segments
    model_cls = MagicMock(return_value=model_instance)

    module = types.ModuleType("pywhispercpp.model")
    module.Model = model_cls
    pkg = types.ModuleType("pywhispercpp")
    pkg.model = module
    monkeypatch.setitem(sys.modules, "pywhispercpp", pkg)
    monkeypatch.setitem(sys.modules, "pywhispercpp.model", module)
    return model_cls, model_instance


def test_satisfies_protocol(monkeypatch):
    _install_fake_pywhispercpp(monkeypatch, [])
    t = WhisperCppTranscriber(modelname="tiny")
    assert isinstance(t, Transcriber)


def test_transcribe_builds_segments_and_text(monkeypatch):
    _, _ = _install_fake_pywhispercpp(
        monkeypatch,
        [_Seg(0, 150, "hallo"), _Seg(150, 300, " welt")],
    )
    t = WhisperCppTranscriber(modelname="large-v3")
    result = t.transcribe(np.zeros(16000, dtype=np.float32), language="de")

    assert result["text"] == "hallo welt"
    assert result["language"] == "de"
    # t0/t1 are 10ms units -> seconds
    assert result["segments"][0]["start"] == 0.0
    assert result["segments"][0]["end"] == 1.5


def test_initial_prompt_and_task_passed_through(monkeypatch):
    _, model = _install_fake_pywhispercpp(monkeypatch, [])
    t = WhisperCppTranscriber(modelname="large-v3")
    t.transcribe(np.zeros(16000), initial_prompt="Befund", task="translate")

    _, kwargs = model.transcribe.call_args
    assert kwargs["initial_prompt"] == "Befund"
    assert kwargs["translate"] is True
    assert kwargs["language"] == "de"
