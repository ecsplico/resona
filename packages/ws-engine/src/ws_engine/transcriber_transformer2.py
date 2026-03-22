import logging
import numpy as np
from decouple import config
from transformers import pipeline

log = logging.getLogger(__name__)

DEFAULT_MODEL_NAME: str = config("DEFAULT_TRANSFORMER_MODEL")


class TransformerTranscriber:
    """Alternate HuggingFace Transformers backend with ``initial_prompt`` support.

    Similar to :class:`~ws_engine.transcriber_transformer.TransformerTranscriber`
    but passes ``initial_prompt`` directly to the pipeline, enabling vocabulary
    hints on models that support it.

    Configure via ``DEFAULT_TRANSFORMER_MODEL`` env var and ``ASR_MODE=whisper-tf``.
    """

    def __init__(self, device: str = "cpu", modelname=DEFAULT_MODEL_NAME):
        self.modelname = modelname
        log.info(f"Loading Transformer model: {DEFAULT_MODEL_NAME} on {device}...")
        self.model = pipeline("automatic-speech-recognition", model=DEFAULT_MODEL_NAME, device=device, chunk_length_s=30)

    def get_model(self):
        return self.model

    def transcribe(self, audio: np.ndarray, **options_dict):
        initial_prompt_str = options_dict.get("initial_prompt", "")

        batch_size = options_dict.get("batch_size", 1)
        return_timestamps = options_dict.get("return_timestamps", True)

        output = self.model(audio, batch_size=batch_size, return_timestamps=return_timestamps, initial_prompt=initial_prompt_str)

        log.info(f"Transformer Output: {output}")
        language = output.get("language", options_dict.get("language", "de"))

        result = {
            "language": language,
            "segments": output.get("chunks", []),
            "text": output.get("text", ""),
        }
        return result
