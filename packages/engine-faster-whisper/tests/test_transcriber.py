from unittest.mock import patch, MagicMock
import numpy as np
from resona_asr_core.protocol import Transcriber
from resona_engine_faster_whisper.transcriber import FastWhisperTranscriber


def _mock_segment(text="hello", start=0.0, end=1.0):
    seg = MagicMock()
    seg.text = text
    seg.start = start
    seg.end = end
    seg.words = None
    return seg


def _info(language="de"):
    info = MagicMock()
    info.language = language
    return info


@patch("resona_engine_faster_whisper.transcriber.BatchedInferencePipeline")
@patch("resona_engine_faster_whisper.transcriber.WhisperModel")
def test_satisfies_protocol(mock_model_cls, mock_pipeline_cls):
    t = FastWhisperTranscriber(device="cpu", modelname="tiny")
    assert isinstance(t, Transcriber)


@patch("resona_engine_faster_whisper.transcriber.BatchedInferencePipeline")
@patch("resona_engine_faster_whisper.transcriber.WhisperModel")
def test_transcribe_returns_expected_keys(mock_model_cls, mock_pipeline_cls):
    pipeline = mock_pipeline_cls.return_value
    pipeline.transcribe.return_value = (iter([_mock_segment()]), _info("de"))

    t = FastWhisperTranscriber(device="cpu", modelname="tiny")
    result = t.transcribe(np.zeros(16000, dtype=np.float32), language="de")

    assert "text" in result
    assert "language" in result
    assert "segments" in result
    assert result["language"] == "de"
    assert "hello" in result["text"]


@patch("resona_engine_faster_whisper.transcriber.BatchedInferencePipeline")
@patch("resona_engine_faster_whisper.transcriber.WhisperModel")
def test_batched_pipeline_used_by_default(mock_model_cls, mock_pipeline_cls):
    """With batching on (default), the batched pipeline handles transcription."""
    pipeline = mock_pipeline_cls.return_value
    pipeline.transcribe.return_value = (iter([]), _info("de"))

    t = FastWhisperTranscriber(device="cpu", modelname="tiny")
    t.transcribe(np.zeros(16000), initial_prompt="test prompt")

    pipeline.transcribe.assert_called_once()
    _, call_kwargs = pipeline.transcribe.call_args
    assert call_kwargs.get("initial_prompt") == "test prompt"
    assert "batch_size" in call_kwargs
    # condition_on_previous_text defaults off for throughput/stability
    assert call_kwargs.get("condition_on_previous_text") is False
    mock_model_cls.return_value.transcribe.assert_not_called()


@patch("resona_engine_faster_whisper.transcriber.BatchedInferencePipeline")
@patch("resona_engine_faster_whisper.transcriber.WhisperModel")
def test_word_timestamps_falls_back_to_sequential(mock_model_cls, mock_pipeline_cls):
    """word_timestamps is unsupported by the batched pipeline -> sequential path."""
    model = mock_model_cls.return_value
    model.transcribe.return_value = (iter([_mock_segment()]), _info("de"))

    t = FastWhisperTranscriber(device="cpu", modelname="tiny")
    t.transcribe(np.zeros(16000), word_timestamps=True)

    model.transcribe.assert_called_once()
    _, call_kwargs = model.transcribe.call_args
    assert call_kwargs.get("word_timestamps") is True
    mock_pipeline_cls.return_value.transcribe.assert_not_called()


@patch("resona_engine_faster_whisper.transcriber.BatchedInferencePipeline")
@patch("resona_engine_faster_whisper.transcriber.WhisperModel")
def test_mps_device_falls_back_to_cpu(mock_model_cls, mock_pipeline_cls):
    """CTranslate2 has no MPS backend; an mps request must downgrade to cpu."""
    FastWhisperTranscriber(device="mps", modelname="tiny")
    _, call_kwargs = mock_model_cls.call_args
    assert call_kwargs.get("device") == "cpu"


@patch("resona_engine_faster_whisper.transcriber.preload_cuda_libs")
@patch("resona_engine_faster_whisper.transcriber.BatchedInferencePipeline")
@patch("resona_engine_faster_whisper.transcriber.WhisperModel")
def test_cuda_device_preloads_libs(mock_model_cls, mock_pipeline_cls, mock_preload):
    FastWhisperTranscriber(device="cuda", modelname="tiny")
    mock_preload.assert_called_once_with()


@patch("resona_engine_faster_whisper.transcriber.preload_cuda_libs")
@patch("resona_engine_faster_whisper.transcriber.BatchedInferencePipeline")
@patch("resona_engine_faster_whisper.transcriber.WhisperModel")
def test_cpu_device_does_not_preload_libs(mock_model_cls, mock_pipeline_cls, mock_preload):
    FastWhisperTranscriber(device="cpu", modelname="tiny")
    mock_preload.assert_not_called()


@patch("resona_engine_faster_whisper.transcriber.BatchedInferencePipeline")
@patch("resona_engine_faster_whisper.transcriber.WhisperModel")
def test_low_batched_coverage_falls_back_to_sequential(mock_model_cls, mock_pipeline_cls):
    """If the batched VAD covers too little of a long clip, re-run sequentially."""
    # 30s of audio, but batched returns a single 1s segment -> ~3% coverage.
    pipeline = mock_pipeline_cls.return_value
    pipeline.transcribe.return_value = (iter([_mock_segment("partial", 0.0, 1.0)]), _info("de"))
    model = mock_model_cls.return_value
    model.transcribe.return_value = (
        iter([_mock_segment("full transcript", 0.0, 30.0)]), _info("de"),
    )

    t = FastWhisperTranscriber(device="cpu", modelname="tiny")
    result = t.transcribe(np.zeros(30 * 16000, dtype=np.float32), language="de")

    pipeline.transcribe.assert_called_once()
    model.transcribe.assert_called_once()  # sequential fallback ran
    _, seq_kwargs = model.transcribe.call_args
    assert seq_kwargs.get("vad_filter") is False
    assert "full transcript" in result["text"]


@patch("resona_engine_faster_whisper.transcriber.BatchedInferencePipeline")
@patch("resona_engine_faster_whisper.transcriber.WhisperModel")
def test_good_batched_coverage_keeps_batched_result(mock_model_cls, mock_pipeline_cls):
    """Healthy coverage on long audio must NOT trigger the sequential fallback."""
    pipeline = mock_pipeline_cls.return_value
    pipeline.transcribe.return_value = (
        iter([_mock_segment("good", 0.0, 28.0)]), _info("de"),
    )

    t = FastWhisperTranscriber(device="cpu", modelname="tiny")
    result = t.transcribe(np.zeros(30 * 16000, dtype=np.float32), language="de")

    pipeline.transcribe.assert_called_once()
    mock_model_cls.return_value.transcribe.assert_not_called()
    assert "good" in result["text"]
