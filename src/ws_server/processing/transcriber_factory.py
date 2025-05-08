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
    log.info(f'Using {MODE} Mode. Using {device} device.')
    if MODE == "faster-whisper":
        MODEL = config("DEFAULT_FASTWHISPER_MODEL")
        print (f"Loaded DEFAULT_FASTWHISPER_MODEL: {MODEL}")
        t = FastWhisperTranscriber(device=device)
    elif MODE == "whisper-tf": # Assuming 'whisper-tf' corresponds to TransformerTranscriber
        MODEL = config("DEFAULT_TRANSFORMER_MODEL")
        print (f"Loaded DEFAULT_TRANSFORMER_MODEL: {MODEL}")
        t = TransformerTranscriber(device=device)
    else: # Default to standard Whisper
        MODEL = config("DEFAULT_WHISPER_MODEL")
        print (f"Loaded DEFAULT_WHISPER_MODEL: {MODEL}")  # Log the loaded value
        t = WhisperTranscriber(device=device)
    return t