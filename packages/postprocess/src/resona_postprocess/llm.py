"""LLM-based postprocessing via litellm."""

import json as _json
import logging
import os

os.environ.setdefault("LITELLM_LOG", "ERROR")

try:
    import litellm
except ImportError:  # pragma: no cover
    litellm = None  # type: ignore[assignment]

from decouple import config

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 120.0


class LLMUnavailableError(RuntimeError):
    """Raised when an LLM step is requested but litellm is not installed."""


def _completion(*, model, api_base, messages, temperature, max_tokens,
                response_format=None):
    """Call litellm.completion with one retry on transient failure."""
    if litellm is None:
        raise LLMUnavailableError(
            "LLM postprocessing requires the 'litellm' package. "
            "Install it: pip install litellm"
        )
    kwargs = {
        "model": model,
        "api_base": api_base,
        "messages": messages,
        "timeout": _DEFAULT_TIMEOUT,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if response_format is not None:
        kwargs["response_format"] = response_format

    last_exc = None
    for attempt in (1, 2):
        try:
            resp = litellm.completion(**kwargs)
            usage = getattr(resp, "usage", None)
            log.info("LLM call model=%s attempt=%d usage=%s", model, attempt, usage)
            return resp.choices[0].message.content
        except LLMUnavailableError:
            raise
        except Exception as e:  # noqa: BLE001 — litellm raises many error types
            last_exc = e
            log.warning("LLM call failed (attempt %d): %s", attempt, e)
    raise last_exc


def llm_transform(
    text: str,
    *,
    prompt: str,
    model: str | None = None,
    api_base: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """Send transcript text through an LLM and return the transformed text."""
    model = model or config("RESONA_LLM_MODEL", default="gpt-4o-mini")
    api_base = api_base or config("RESONA_LLM_API_BASE", default="") or None
    return _completion(
        model=model,
        api_base=api_base,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )


# Backwards-compatible alias for the original entry point.
def llm_postprocess(text: str, *, prompt: str, model: str | None = None,
                    api_base: str | None = None) -> str:
    """Deprecated alias for :func:`llm_transform`."""
    return llm_transform(text, prompt=prompt, model=model, api_base=api_base)


def llm_extract(
    text: str,
    *,
    prompt: str,
    model: str | None = None,
    api_base: str | None = None,
    temperature: float | None = None,
) -> dict:
    """Extract structured data from text. Returns a parsed JSON object.

    On a malformed LLM response, returns ``{"_raw": <response string>}`` so a
    job never hard-fails on a bad extraction.
    """
    model = model or config("RESONA_LLM_MODEL", default="gpt-4o-mini")
    api_base = api_base or config("RESONA_LLM_API_BASE", default="") or None
    raw = _completion(
        model=model,
        api_base=api_base,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
        temperature=temperature,
        max_tokens=None,
        response_format={"type": "json_object"},
    )
    try:
        parsed = _json.loads(raw)
    except (_json.JSONDecodeError, TypeError):
        return {"_raw": raw}
    return parsed if isinstance(parsed, dict) else {"_raw": raw}
