import logging
import numpy as np
from decouple import config
from transformers import pipeline, AutoProcessor
from core.db.utils import get_active_initial_prompts_string # Import the utility function from db utils
log = logging.getLogger(__name__)

DEFAULT_MODEL_NAME: str = config("DEFAULT_TRANSFORMER_MODEL")

class TransformerTranscriber:
    def __init__(self, device: str = "cpu", modelname=DEFAULT_MODEL_NAME):
        self.modelname = modelname
        # Consider lazy loading
        log.info(f"Loading Transformer model: {DEFAULT_MODEL_NAME} on {device}...")  # Use pipeline for lazy loading if startup time is critical.
        self.model = pipeline("automatic-speech-recognition", model=DEFAULT_MODEL_NAME, device=device, chunk_length_s=30)

    def get_model(self):
        # Note: original returned self.model.model
        return self.model

    def transcribe(self, audio: np.ndarray, **options_dict):
        # Construct generate_kwargs for the pipeline
        generate_kwargs = {}
        
        # Get active initial prompts from the database
        initial_prompt_str = get_active_initial_prompts_string()
        if initial_prompt_str:
            # generate_kwargs["initial_prompt"] = initial_prompt_str
            # TODO: initial_prompt is not directly supported by pipeline generate in this way. 
            # Needs to be converted to tokens or handled differently.
            pass
        else:
            log.info("No active initial prompts found or database error.")

        # Extract language and task from options_dict
        language = options_dict.get("language")
        task = options_dict.get("task")

        if language:
            generate_kwargs["language"] = language
        if task:
            generate_kwargs["task"] = task

        # Consider adding batch_size and return_timestamps from options_dict if needed
        batch_size = options_dict.get("batch_size", 1)
        return_timestamps = options_dict.get("return_timestamps", True) # Keep original default

        # Pass generate_kwargs to the pipeline
        output = self.model(
            audio, 
            batch_size=batch_size, 
            return_timestamps=return_timestamps, 
            generate_kwargs=generate_kwargs
        )

        log.info(f"Transformer Output: {output}")
        
        # Determine language - pipeline might return it in chunks or we rely on what we passed/detected
        # If we passed language, we can assume it was used. If detecting, pipeline typically doesn't return 
        # a top-level language key in this return format easily unless checking chunks.
        # But 'output' from pipeline usually has 'text' and 'chunks'. 
        
        result = {
            "language": language if language else "unknown", # Best effort or need to parse from output if not provided
            "segments": output.get("chunks", []),
            "text": output.get("text", ""),
        }
        return result