"""LLM-based postprocessing via litellm."""

import logging

try:
    import litellm
except ImportError:  # pragma: no cover
    litellm = None  # type: ignore[assignment]

from decouple import config

log = logging.getLogger(__name__)


def llm_postprocess(
    text: str,
    *,
    prompt: str,
    model: str | None = None,
    api_base: str | None = None,
) -> str:
    """Send transcript through an LLM with a system prompt.

    Args:
        text: Raw transcript text.
        prompt: System prompt describing the desired transformation.
        model: litellm model string (e.g. 'ollama/llama3', 'gpt-4o').
               Falls back to RESONA_LLM_MODEL env var, then 'gpt-4o-mini'.
        api_base: Custom API endpoint. Falls back to RESONA_LLM_API_BASE env var.

    Returns:
        Transformed text from the LLM.
    """
    model = model or config("RESONA_LLM_MODEL", default="gpt-4o-mini")
    api_base = api_base or config("RESONA_LLM_API_BASE", default="") or None

    log.info(f"LLM postprocess: model={model}, prompt={prompt[:50]}...")

    response = litellm.completion(
        model=model,
        api_base=api_base,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
    )

    return response.choices[0].message.content
