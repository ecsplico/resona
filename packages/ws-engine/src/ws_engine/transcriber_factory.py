import logging
import torch
from decouple import config

from .transcriber_whisper import WhisperTranscriber
from .transcriber_transformer import TransformerTranscriber
from .transcriber_fast_whisper import FastWhisperTranscriber

MODE: str = config("ASR_MODE")
log = logging.getLogger(__name__)


def getTranscriber():
    """Factory function to instantiate the appropriate transcriber based on ASR_MODE."""
    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info(f'Using {MODE} Mode. Using {device} device.')
    if MODE == "faster-whisper":
        MODEL = config("DEFAULT_FASTWHISPER_MODEL")
        log.info(f"Loaded DEFAULT_FASTWHISPER_MODEL: {MODEL}")
        t = FastWhisperTranscriber(device=device)
    elif MODE == "whisper-tf":
        MODEL = config("DEFAULT_TRANSFORMER_MODEL")
        log.info(f"Loaded DEFAULT_TRANSFORMER_MODEL: {MODEL}")
        t = TransformerTranscriber(device=device)
    else:
        MODEL = config("DEFAULT_WHISPER_MODEL")
        log.info(f"Loaded DEFAULT_WHISPER_MODEL: {MODEL}")
        t = WhisperTranscriber(device=device)
    return t
