import numpy as np
from decouple import config
from faster_whisper import WhisperModel
import logging

log = logging.getLogger(__name__)

DEFAULT_MODEL_NAME: str = config("DEFAULT_FASTWHISPER_MODEL", cast=str)


class FastWhisperTranscriber:
    def __init__(self, device: str = "cpu", modelname=DEFAULT_MODEL_NAME):
        self.modelname = modelname
        compute_type = "int8_float16" if device == "cuda" else "int8"
        log.info(f"Loading FastWhisper model: {DEFAULT_MODEL_NAME} on {device} with compute type: {compute_type}...")
        self.model = WhisperModel(DEFAULT_MODEL_NAME, device=device, compute_type=compute_type)

    def get_model(self):
        return self.model

    def transcribe(self, audio: np.ndarray, **kwargs) -> dict:
        # Use initial_prompt from caller as-is (no DB lookup)
        initial_prompt = kwargs.get("initial_prompt")
        if not initial_prompt:
            kwargs.pop("initial_prompt", None)

        segments = []
        text = ""
        segment_generator, info = self.model.transcribe(audio, beam_size=kwargs.get('beam_size', 5), **kwargs)
        for segment in segment_generator:
            segments.append(segment)
            text = text + segment.text
        result = {
            "language": info.language,
            "segments": segments,
            "text": text,
        }
        return result
