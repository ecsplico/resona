import logging
from threading import Lock
from typing import BinaryIO, Union
from timeit import default_timer as timer

import ffmpeg
import numpy as np
import whisper # Keep for whisper.load_audio

# Import necessary components from new locations
from .transcriber_factory import getTranscriber
from .formatting import toMarkdown # Needed for markdown conversion within run_asr
# Removed database imports to break circular dependency
# from .db.engine import engine
# from .db.models import InitialPrompt
# from sqlmodel import Session, select

# Constants (consider moving to a config file or core module)
SAMPLE_RATE = 16000

# Global lock for model access (if needed across different functions)
model_lock = Lock()

log = logging.getLogger('uvicorn.test') # Or use a dedicated logger


# Moved get_active_initial_prompts_string to src/db/utils.py to resolve circular import


def run_asr(file: Union[str, BinaryIO], markdown: bool = True, task: str = "transcribe", language: str = "de", **asr_options) -> dict:
    """
    Loads audio, runs transcription using the configured ASR model,
    and optionally converts the result to markdown.

    Args:
        file: Path to the audio file or a binary file object.
        markdown: Whether to convert the result text to markdown.
        task: The ASR task ('transcribe' or 'translate').
        language: The language code for ASR.
        **asr_options: Additional options passed directly to the transcriber's transcribe method.

    Returns:
        A dictionary containing the transcription result.
    """
    log.info(f"Running ASR: task='{task}', language='{language}', markdown={markdown}")
    try:
        # Load audio
        if isinstance(file, str):
            # Use whisper.load_audio for file paths
            audio = whisper.load_audio(file, sr=SAMPLE_RATE)
            log.info(f"Loaded audio from path: {file}")
        else:
            # Use custom load_audio for file-like objects
            audio = load_audio(file, sr=SAMPLE_RATE)
            log.info("Loaded audio from file object.")

        # Prepare options for the transcriber
        options_dict = {"task": task, "language": language, **asr_options}

        result = {}
        with model_lock: # Ensure thread-safe access to the model
            T = getTranscriber() # Get the instantiated transcriber
            start = timer()
            log.info(f"Starting transcription with options: {options_dict}")
            result: dict = T.transcribe(audio, **options_dict) # Pass all options
            duration = timer() - start
            log.info(f"ASR finished in {duration:.2f} seconds. Language: {result.get('language', 'N/A')}")

            # Optional Markdown conversion
            if markdown and "text" in result:
                log.info("Converting result text to markdown...")
                result["md"] = toMarkdown(result["text"])
                log.info("Markdown conversion complete.")
            elif markdown:
                 log.warning("Markdown conversion requested, but no 'text' found in result.")


        return result

    except Exception as e:
        log.error(f"Error during ASR processing: {e}", exc_info=True)
        # Re-raise as a RuntimeError to be caught by the calling task processor
        raise RuntimeError(f"ASR processing failed: {e}") from e


def load_audio(file: BinaryIO, encode=True, sr: int = SAMPLE_RATE):
    """
    Open an audio file object and read as mono waveform, resampling as necessary.
    Modified from https://github.com/openai/whisper/blob/main/whisper/audio.py
    """
    if encode:
        try:
            # Decode audio using ffmpeg
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
        # Read raw audio data if not encoding
        out = file.read()

    # Convert buffer to float32 numpy array
    waveform = np.frombuffer(out, np.int16).flatten().astype(np.float32) / 32768.0
    log.info(f"Audio loaded successfully. Shape: {waveform.shape}, Sample Rate: {sr}")
    return waveform
