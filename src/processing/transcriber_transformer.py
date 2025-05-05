import logging
import numpy as np
from decouple import config
from transformers import pipeline

# Assuming MODEL_NAME is defined globally or passed differently
MODEL_NAME: str = config("ASR_MODEL", cast=str)
log = logging.getLogger('uvicorn.test') # Or use a dedicated logger

class TransformerTranscriber:
    def __init__(self, device: str = "cpu"):
        # Consider lazy loading
        self.model = pipeline("automatic-speech-recognition", model=MODEL_NAME, device=device, chunk_length_s=30)

    def get_model(self):
        # Note: original returned self.model.model
        return self.model

    def transcribe(self, audio: np.ndarray, **options_dict):
        # Note: options_dict is not used here in the original code
        # Consider adding batch_size and return_timestamps to options_dict if needed
        output = self.model(audio, batch_size=1, return_timestamps=True)
        log.info(f"Transformer Output: {output}")
        # Adapt the output format to be consistent with other transcribers if necessary
        result = {
            "language": "de", # Assuming German, might need to detect or pass language
            "segments": output["chunks"],
            "text": output["text"],
        }
        return result