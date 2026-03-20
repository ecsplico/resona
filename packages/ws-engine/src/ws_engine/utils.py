import logging
from threading import Lock
from typing import BinaryIO, Union

from decouple import config
import ffmpeg
import numpy as np
import whisper

from .transcriber_factory import getTranscriber

SAMPLE_RATE = 16000
model_lock = Lock()
log = logging.getLogger(__name__)


def run_asr(file: Union[str, BinaryIO], task: str = "transcribe", language: str = "de", **asr_options) -> dict:
    """Loads audio and runs transcription using the configured ASR model."""
    log.info(f"Running ASR: task='{task}', language='{language}'")
    try:
        if isinstance(file, str):
            audio = whisper.load_audio(file, sr=SAMPLE_RATE)
            log.info(f"Loaded audio from path: {file}")
        else:
            audio = load_audio(file, sr=SAMPLE_RATE)
            log.info("Loaded audio from file object.")

        options_dict = {"task": task, "language": language, **asr_options}

        result = {}
        with model_lock:
            T = getTranscriber()
            from timeit import default_timer as timer
            start = timer()
            log.info(f"Starting transcription with options: {options_dict}")
            result: dict = T.transcribe(audio, **options_dict)
            duration = timer() - start
            log.info(f"ASR finished in {duration:.2f} seconds. Language: {result.get('language', 'N/A')}")

        return result

    except Exception as e:
        log.error(f"Error during ASR processing: {e}", exc_info=True)
        raise RuntimeError(f"ASR processing failed: {e}") from e


def load_audio(file: BinaryIO, encode=True, sr: int = SAMPLE_RATE):
    """Open an audio file object and read as mono waveform, resampling as necessary."""
    if encode:
        try:
            out, _ = (
                ffmpeg.input("pipe:", threads=0)
                .output("-", format="s16le", acodec="pcm_s16le", ac=1, ar=sr)
                .run(cmd="ffmpeg", capture_stdout=True, capture_stderr=True, input=file.read())
            )
        except ffmpeg.Error as e:
            stderr = e.stderr.decode()
            log.error(f"FFmpeg error during audio loading: {stderr}")
            raise RuntimeError(f"Failed to load audio: {stderr}") from e
        except Exception as e:
            log.error(f"Unexpected error during audio loading: {e}")
            raise RuntimeError(f"Failed to load audio: {e}") from e
    else:
        out = file.read()

    waveform = np.frombuffer(out, np.int16).flatten().astype(np.float32) / 32768.0
    log.info(f"Audio loaded successfully. Shape: {waveform.shape}, Sample Rate: {sr}")
    return waveform
