import whisper
import numpy as np
from decouple import config

# Assuming MODEL_NAME is defined globally or passed differently
# For now, getting it from config here
MODEL_NAME: str = config("ASR_MODEL", cast=str)

class WhisperTranscriber:
    def __init__(self, device: str = "cpu"):
        # Consider lazy loading the model if startup time is critical
        self.model = whisper.load_model(MODEL_NAME, device=device)

    def get_model(self):
        return self.model

    def transcribe(self, audio: np.ndarray, **options_dict):
        # Note: Original whisper transcribe returns a dict, ensure consistency if needed
        return self.model.transcribe(audio, **options_dict)