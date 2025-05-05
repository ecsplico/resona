import logging
import numpy as np
from decouple import config
from transformers import pipeline
from ..db.utils import get_active_initial_prompts_string # Import the utility function from db utils

# Assuming MODEL_NAME is defined globally or passed differently
MODEL_NAME: str = config("DEFAULT_WHISPERTF_MODEL", cast=str)
log = logging.getLogger('uvicorn.test') # Or use a dedicated logger

class TransformerTranscriber:
    def __init__(self, device: str = "cpu", modelname=MODEL_NAME):
        self.modelname = modelname
        # Consider lazy loading
        self.model = pipeline("automatic-speech-recognition", model=MODEL_NAME, device=device, chunk_length_s=30)

    def get_model(self):
        # Note: original returned self.model.model
        return self.model

    def transcribe(self, audio: np.ndarray, **options_dict):
        # Note: options_dict is not directly used by the pipeline call itself,
        # but we check it for consistency or potential future use.
        # We will use generate_kwargs for passing prompts if applicable.

        generate_kwargs = {}
        # Get active initial prompts from the database
        initial_prompt_str = get_active_initial_prompts_string()
        if initial_prompt_str:
            # Add the prompt string to generate_kwargs if it's not empty
            # This might work if the underlying model supports it (e.g., Whisper)
            generate_kwargs["prompt_ids"] = self.model.tokenizer(initial_prompt_str, return_tensors="pt").input_ids
            log.info(f"Attempting to use initial prompt via generate_kwargs: '{initial_prompt_str}'")
        else:
            log.info("No active initial prompts found or database error.")


        # Consider adding batch_size and return_timestamps from options_dict if needed
        batch_size = options_dict.get("batch_size", 1)
        return_timestamps = options_dict.get("return_timestamps", True) # Keep original default

        # Pass generate_kwargs to the pipeline call
        output = self.model(audio, batch_size=batch_size, return_timestamps=return_timestamps, generate_kwargs=generate_kwargs)

        log.info(f"Transformer Output: {output}")
        # Adapt the output format to be consistent with other transcribers if necessary
        # Determine language - pipeline might return it, otherwise default or use options_dict
        language = output.get("language", options_dict.get("language", "de")) # Example logic

        result = {
            "language": language,
            "segments": output.get("chunks", []), # Use .get for safety
            "text": output.get("text", ""), # Use .get for safety
        }
        return result