import logging
import numpy as np
from decouple import config
from transformers import pipeline, AutoProcessor

log = logging.getLogger(__name__)

DEFAULT_MODEL_NAME: str = config("DEFAULT_TRANSFORMER_MODEL")


class TransformerTranscriber:
    def __init__(self, device: str = "cpu", modelname=DEFAULT_MODEL_NAME):
        self.modelname = modelname
        log.info(f"Loading Transformer model: {DEFAULT_MODEL_NAME} on {device}...")
        self.model = pipeline("automatic-speech-recognition", model=DEFAULT_MODEL_NAME, device=device, chunk_length_s=30)

    def get_model(self):
        return self.model

    def transcribe(self, audio: np.ndarray, **options_dict):
        generate_kwargs = {}

        language = options_dict.get("language")
        task = options_dict.get("task")

        if language:
            generate_kwargs["language"] = language
        if task:
            generate_kwargs["task"] = task

        batch_size = options_dict.get("batch_size", 1)
        return_timestamps = options_dict.get("return_timestamps", True)

        output = self.model(
            audio,
            batch_size=batch_size,
            return_timestamps=return_timestamps,
            generate_kwargs=generate_kwargs
        )

        log.info(f"Transformer Output: {output}")

        result = {
            "language": language if language else "unknown",
            "segments": output.get("chunks", []),
            "text": output.get("text", ""),
        }
        return result
