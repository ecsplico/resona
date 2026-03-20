import whisper
import numpy as np
from decouple import config
import logging

log = logging.getLogger(__name__)

DEFAULT_MODEL_NAME: str = config("DEFAULT_WHISPER_MODEL", cast=str)


class WhisperTranscriber:
    def __init__(self, device: str = "cpu", modelname=DEFAULT_MODEL_NAME):
        self.modelname = modelname
        log.info(f"Loading Whisper model: {DEFAULT_MODEL_NAME} on {device}...")
        self.model = whisper.load_model(self.modelname, device=device)

    def get_model(self):
        return self.model

    def transcribe(self, audio: np.ndarray, **options_dict):
        # Use initial_prompt from caller as-is (no DB lookup)
        initial_prompt = options_dict.get("initial_prompt")
        if not initial_prompt:
            options_dict.pop("initial_prompt", None)

        return self.model.transcribe(audio, **options_dict)
