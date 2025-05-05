import numpy as np
from decouple import config
from faster_whisper import WhisperModel

# Assuming MODEL_NAME is defined globally or passed differently
MODEL_NAME: str = config("ASR_MODEL", cast=str)

class FastWhisperTranscriber:
    def __init__(self, device: str = "cpu"):
        # Determine compute type based on device
        compute_type = "int8_float16" if device == "cuda" else "int8"
        # Consider lazy loading
        self.model = WhisperModel(MODEL_NAME, device=device, compute_type=compute_type)

    def get_model(self):
        return self.model

    def transcribe(self, audio: np.ndarray, **kwargs) -> dict:
        segments = []
        text = ""
        # Pass relevant kwargs like beam_size, language, task, etc.
        # Default beam_size=5 was used in original code
        segment_generator, info = self.model.transcribe(audio, beam_size=kwargs.get('beam_size', 5), **kwargs)
        for segment in segment_generator:
            # segment object likely has start, end, text attributes
            segments.append(segment) # Store the segment object directly
            text = text + segment.text # Concatenate text
        result = {
            "language": info.language,
            "segments": segments, # List of segment objects
            "text": text,
        }
        return result