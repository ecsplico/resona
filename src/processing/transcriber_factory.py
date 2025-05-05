import logging
import torch
from decouple import config

# Import the transcriber classes
from .transcriber_whisper import WhisperTranscriber
from .transcriber_transformer import TransformerTranscriber
from .transcriber_fast_whisper import FastWhisperTranscriber

# Get config values
MODE: str = config("ASR_MODE")
log = logging.getLogger('uvicorn.test') # Or use a dedicated logger

def getTranscriber():
    """
    Factory function to instantiate the appropriate transcriber based on ASR_MODE.
    """
    # Determine device (consider moving this logic if used elsewhere)
    # Original code had 'and False' disabling CUDA, removed that assumption
    device = "cuda" if torch.cuda.is_available() else "cpu"

    if MODE == "faster-whisper":
        t = FastWhisperTranscriber(device=device)
        MODEL_NAME: str = config("DEFAULT_FASTWHISPER_MODEL", cast=str)
    elif MODE == "whisper-tf": # Assuming 'whisper-tf' corresponds to TransformerTranscriber
        t = TransformerTranscriber(device=device)
        MODEL_NAME: str = config("DEFAULT_WHISPERTF_MODEL", cast=str)
    else: # Default to standard Whisper
        t = WhisperTranscriber(device=device)
        MODEL_NAME: str = config("DEFAULT_WHISPER_MODEL", cast=str)


    log.info(f'Loading model {MODEL_NAME} into {device} using {MODE}')
    
    return t