import numpy as np
from decouple import config
from faster_whisper import WhisperModel
import logging # Import logging
from core.db.utils import get_active_initial_prompts_string # Import the utility function from db utils

log = logging.getLogger(__name__)

# Assuming MODEL_NAME is defined globally or passed differently
DEFAULT_MODEL_NAME: str = config("DEFAULT_FASTWHISPER_MODEL", cast=str)

class FastWhisperTranscriber:
    def __init__(self, device: str = "cpu", modelname=DEFAULT_MODEL_NAME):
        self.modelname = modelname
        # Determine compute type based on device
        compute_type = "int8_float16" if device == "cuda" else "int8"
        # Consider lazy loading
        log.info(f"Loading FastWhisper model: {DEFAULT_MODEL_NAME} on {device} with compute type: {compute_type}...")
        self.model = WhisperModel(DEFAULT_MODEL_NAME, device=device, compute_type=compute_type)

    def get_model(self):
        return self.model

    def transcribe(self, audio: np.ndarray, **kwargs) -> dict:
        # Get active initial prompts from the database
        initial_prompt_str = get_active_initial_prompts_string()
        if initial_prompt_str:
            # Add the prompt string to the kwargs if it's not empty
            kwargs["initial_prompt"] = initial_prompt_str
            # log.info(f"Using initial prompt: '{initial_prompt_str}'")
        else:
            # Remove initial_prompt if it exists but the DB fetch returned empty
            kwargs.pop("initial_prompt", None)
            # log.info("No active initial prompts found or database error.")

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