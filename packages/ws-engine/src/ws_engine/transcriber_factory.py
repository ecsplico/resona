import logging
from threading import Lock
import torch
from decouple import config

MODE: str = config("ASR_MODE")
log = logging.getLogger(__name__)

_transcriber = None
_init_lock = Lock()


def getTranscriber():
    """Return the singleton transcriber, creating and loading it on first call."""
    global _transcriber
    if _transcriber is None:
        with _init_lock:
            if _transcriber is None:
                device = "cuda" if torch.cuda.is_available() else "cpu"
                log.info(f"Using {MODE} Mode. Using {device} device.")
                if MODE == "faster-whisper":
                    from .transcriber_fast_whisper import FastWhisperTranscriber
                    MODEL = config("DEFAULT_FASTWHISPER_MODEL")
                    log.info(f"Loading DEFAULT_FASTWHISPER_MODEL: {MODEL}")
                    _transcriber = FastWhisperTranscriber(device=device)
                elif MODE == "whisper-tf":
                    from .transcriber_transformer import TransformerTranscriber
                    MODEL = config("DEFAULT_TRANSFORMER_MODEL")
                    log.info(f"Loading DEFAULT_TRANSFORMER_MODEL: {MODEL}")
                    _transcriber = TransformerTranscriber(device=device)
                else:
                    from .transcriber_whisper import WhisperTranscriber
                    MODEL = config("DEFAULT_WHISPER_MODEL")
                    log.info(f"Loading DEFAULT_WHISPER_MODEL: {MODEL}")
                    _transcriber = WhisperTranscriber(device=device)
                log.info("Transcriber ready.")
    return _transcriber
