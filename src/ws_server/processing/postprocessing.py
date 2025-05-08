import logging
from typing import Callable, Dict, List

# Import postprocessing functions
from .postprocessing_markdown import process_markdown

log = logging.getLogger('uvicorn.test')

# Registry of available postprocessors
# The key is the name used in configuration, value is the function itself.
POSTPROCESSORS: Dict[str, Callable[[dict], dict]] = {
    "markdown": process_markdown,
    # Add other postprocessors here as they are created
    # "another_postprocessor": another_module.process_function,
}

def apply_postprocessing_steps(result: dict, steps: List[str]) -> dict:
    """
    Applies a list of named postprocessing steps to the ASR result.

    Args:
        result: The initial ASR result dictionary.
        steps: A list of strings, where each string is a key from
               the POSTPROCESSORS dictionary.

    Returns:
        The ASR result dictionary after all specified postprocessing
        steps have been applied.
    """
    if not steps:
        log.info("No postprocessing steps configured.")
        return result

    log.info(f"Applying postprocessing steps: {steps}")
    current_result = result.copy() # Work on a copy

    for step_name in steps:
        processor_func = POSTPROCESSORS.get(step_name)
        if processor_func:
            try:
                log.info(f"Running postprocessing step: {step_name}")
                current_result = processor_func(current_result)
                log.info(f"Finished postprocessing step: {step_name}")
            except Exception as e:
                log.error(f"Error during postprocessing step '{step_name}': {e}", exc_info=True)
                # Decide on error handling: stop processing, skip step, or mark error in result
                # For now, we'll log and continue with potentially partially processed data.
        else:
            log.warning(f"Unknown postprocessing step configured: {step_name}. Skipping.")
    
    log.info("All configured postprocessing steps applied.")
    return current_result