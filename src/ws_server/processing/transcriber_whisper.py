import whisper
import numpy as np
from decouple import config
import logging # Import logging
from ..db.utils import get_active_initial_prompts_string # Import the utility function from db utils

log = logging.getLogger('uvicorn.test') # Get logger

# Assuming MODEL_NAME is defined globally or passed differently
# For now, getting it from config here
MODEL_NAME: str = config("DEFAULT_WHISPER_MODEL", cast=str)

class WhisperTranscriber:
    def __init__(self, device: str = "cpu", modelname=MODEL_NAME):
        self.modelname = modelname
        # Consider lazy loading the model if startup time is critical
        self.model = whisper.load_model(self.modelname, device=device)

    def get_model(self):
        return self.model

    def transcribe(self, audio: np.ndarray, **options_dict):
        # Get active initial prompts from the database
        initial_prompt_str = get_active_initial_prompts_string()
        if initial_prompt_str:
            # Add the prompt string to the options if it's not empty
            options_dict["initial_prompt"] = initial_prompt_str
            log.info(f"Using initial prompt: '{initial_prompt_str}'")
        else:
            # Remove initial_prompt if it exists but the DB fetch returned empty
            options_dict.pop("initial_prompt", None)
            log.info("No active initial prompts found or database error.")

        # Note: Original whisper transcribe returns a dict, ensure consistency if needed
        return self.model.transcribe(audio, **options_dict)