"""Lightning Whisper MLX transcription backend for Resona.

Batched MLX inference on the Apple Silicon GPU — the fastest option for
long-form audio on a Mac. Same model sizes as the other Whisper engines.

Notes / limitations vs the other engines:
  * ``lightning-whisper-mlx`` transcribes from a file path, so the incoming
    waveform is written to a temporary 16 kHz WAV first.
  * It does not support ``initial_prompt`` — the argument is ignored (a warning
    is logged) so the call still satisfies the Transcriber protocol.

Configure with ``DEFAULT_LIGHTNING_MLX_MODEL`` (``large-v3``, ``distil-large-v3``,
…), ``LIGHTNING_MLX_BATCH_SIZE`` and ``LIGHTNING_MLX_QUANT`` (``none``/``4bit``/``8bit``).

Model weights are stored under the LM Studio model folder (``~/.lmstudio/models``
by default, override with ``LIGHTNING_MLX_MODELS_DIR``) instead of polluting the
process working directory — see ``_models_dir``.
"""

import contextlib
import logging
import os
import tempfile
import wave
from pathlib import Path

import numpy as np
from decouple import config

from resona_asr_core.audio import SAMPLE_RATE
from resona_asr_core.protocol import TranscriptionResult

log = logging.getLogger(__name__)

DEFAULT_MODEL: str = config("DEFAULT_LIGHTNING_MLX_MODEL", default="large-v3")
BATCH_SIZE: int = config("LIGHTNING_MLX_BATCH_SIZE", default=12, cast=int)
_QUANT_RAW: str = config("LIGHTNING_MLX_QUANT", default="none")


def _quant() -> str | None:
    return None if _QUANT_RAW.lower() in ("", "none", "false") else _QUANT_RAW


def _models_dir() -> Path:
    """Base dir for model weights.

    ``lightning-whisper-mlx`` hardcodes ``./mlx_models/<name>`` relative to the
    process CWD for both download and load, with no path parameter — left alone
    it litters the working directory. Point it at the LM Studio model folder
    (``~/.lmstudio/models`` default) so weights live with other local models;
    override with ``LIGHTNING_MLX_MODELS_DIR``.
    """
    raw = config("LIGHTNING_MLX_MODELS_DIR", default="")
    return Path(raw).expanduser() if raw else Path.home() / ".lmstudio" / "models"


@contextlib.contextmanager
def _chdir(path: Path):
    """Temporarily switch CWD so the library's relative ./mlx_models lands under path."""
    prev = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)


def _write_temp_wav(audio: np.ndarray, sr: int = SAMPLE_RATE) -> str:
    """Write a float32 mono waveform to a temp 16-bit PCM WAV, return the path."""
    pcm = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    fd = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(fd, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sr)
        wav.writeframes(pcm.tobytes())
    fd.close()
    return fd.name


class LightningMLXTranscriber:
    """Lightning Whisper MLX backend. Batched GPU inference on Apple Silicon.

    The ``device`` argument is accepted for interface compatibility but ignored —
    MLX always uses the Apple GPU.
    """

    def __init__(self, device: str = "mps", modelname: str | None = None):
        from lightning_whisper_mlx import LightningWhisperMLX  # heavy/optional

        model_name = modelname or DEFAULT_MODEL
        models_base = _models_dir()
        models_base.mkdir(parents=True, exist_ok=True)
        log.info(
            "Loading Lightning Whisper MLX model: %s (batch_size=%s, quant=%s) under %s",
            model_name, BATCH_SIZE, _quant(), models_base,
        )
        # The library downloads to ./mlx_models/<name> relative to CWD; run the
        # constructor with CWD set to the models dir so weights land there.
        with _chdir(models_base):
            self.model = LightningWhisperMLX(
                model=model_name, batch_size=BATCH_SIZE, quant=_quant()
            )
        # Absolute path to the downloaded model, so transcription does not depend
        # on CWD (the library's own transcribe() hardcodes a relative path).
        self._model_path = str((models_base / "mlx_models" / self.model.name).resolve())

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        language: str = "de",
        task: str = "transcribe",
        initial_prompt: str | None = None,
        word_timestamps: bool = False,
        vad_filter: bool = False,
        **kwargs,
    ) -> TranscriptionResult:
        if initial_prompt:
            log.warning("lightning-mlx does not support initial_prompt; ignoring it")

        # Call transcribe_audio directly with the absolute model path rather than
        # self.model.transcribe(), which hardcodes a CWD-relative ./mlx_models path.
        from lightning_whisper_mlx.transcribe import transcribe_audio

        path = _write_temp_wav(audio)
        try:
            result = transcribe_audio(
                path,
                path_or_hf_repo=self._model_path,
                language=language,
                batch_size=BATCH_SIZE,
            )
        finally:
            Path(path).unlink(missing_ok=True)

        segments = []
        for seg in result.get("segments", []) or []:
            # lightning returns [start, end, text] triples.
            if isinstance(seg, (list, tuple)) and len(seg) == 3:
                segments.append({"start": seg[0], "end": seg[1], "text": seg[2]})
            elif isinstance(seg, dict):
                segments.append(seg)

        return TranscriptionResult(
            text=result.get("text", ""),
            language=language,
            segments=segments,
        )
