"""LLM-based postprocessing via litellm.

Every call names an approved :class:`Endpoint` and an :class:`ExecutionPolicy`.
The policy is re-checked on each call, immediately before litellm is reached.
Nothing here falls back to an ambient model or base URL; the endpoint fixes both.
"""

import json as _json
import logging

from decouple import config
from resona_job_core.policy import (
    Endpoint,
    EndpointKind,
    ExecutionPolicy,
    PolicyDenied,
    Stage,
    check_policy,
    is_loopback_url,
)

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 120.0

_UNSET = object()
# Resolved lazily on first use (keeps the heavy `import litellm` out of CLI
# startup). ``_UNSET`` = not yet attempted; ``None`` = attempted/unavailable.
# Exposed at module scope so tests can monkeypatch it directly.
litellm = _UNSET


class LLMUnavailableError(RuntimeError):
    """Raised when an LLM step is requested but litellm is not installed."""


# Convenience localhost defaults for OpenAI-compatible local LLM servers, keyed
# by the litellm model prefix. Lets users run fully offline postprocessing with
# just ``RESONA_LLM_MODEL=lm_studio/qwen2.5`` — no api_base needed. litellm
# already knows Ollama's default base (11434), so it is intentionally absent.
_LOCAL_API_BASE_DEFAULTS = {
    "lm_studio": "http://localhost:1234/v1",   # LM Studio
    "mlx": "http://localhost:10240/v1",        # mlx-omni-server / oMLX
}

# Explicit local-mode default for Ollama models when no base is configured.
_OLLAMA_DEFAULT_BASE = "http://localhost:11434"


def _resolve_api_base(model: str, api_base: str | None) -> str | None:
    """Resolve the api_base: explicit arg > RESONA_LLM_API_BASE > local default.

    For local OpenAI-compatible servers (LM Studio, MLX) a sensible localhost
    default is filled in from the model prefix so offline use needs no extra
    config. Returns None for cloud models (litellm handles those).
    """
    if api_base:
        return api_base
    env = config("RESONA_LLM_API_BASE", default="")
    if env:
        return env
    prefix = model.split("/", 1)[0] if "/" in model else ""
    return _LOCAL_API_BASE_DEFAULTS.get(prefix)


def _resolve_litellm():
    """Import litellm on first use, caching the module (or None) at module scope."""
    global litellm
    if litellm is _UNSET:
        try:
            import litellm as _litellm
            litellm = _litellm
        except ImportError:
            litellm = None
    if litellm is None:
        raise LLMUnavailableError(
            "LLM postprocessing requires the 'litellm' package. "
            "Install it: pip install litellm"
        )
    return litellm


def _completion(*, model, api_base, messages, temperature, max_tokens,
                response_format=None):
    """Call litellm.completion with one retry on transient failure."""
    import os
    os.environ.setdefault("LITELLM_LOG", "ERROR")
    litellm = _resolve_litellm()
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
    endpoint: Endpoint,
    policy: ExecutionPolicy,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """Send transcript text to an approved LLM endpoint and return the result.

    The policy is checked on every call, before litellm is reached. The endpoint
    fixes the model and base URL; no environment fallback applies here.
    """
    check_policy(policy, [Stage(name="llm", kind="llm", endpoint=endpoint)])
    return _completion(
        model=endpoint.model,
        api_base=endpoint.url,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )


def llm_extract(
    text: str,
    *,
    prompt: str,
    endpoint: Endpoint,
    policy: ExecutionPolicy,
    temperature: float | None = None,
) -> dict:
    """Extract structured data from text. Returns a parsed JSON object.

    On a malformed LLM response, returns ``{"_raw": <response string>}`` so a
    job never hard-fails on a bad extraction.
    """
    check_policy(policy, [Stage(name="llm", kind="llm", endpoint=endpoint)])
    raw = _completion(
        model=endpoint.model,
        api_base=endpoint.url,
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


# --- Legacy local-mode bridge -------------------------------------------------
# Profiles whose llm/extract steps carry only ``model``/``api_base`` (no registry
# ``endpoint`` id) can run only in explicit local CLI mode. The endpoint is derived
# here from the step; the hub never uses these helpers.


def _legacy_model_and_url(step: dict) -> tuple[str, str | None]:
    model = step.get("model") or config("RESONA_LLM_MODEL", default="")
    if not model:
        raise PolicyDenied("llm", "no model configured")
    url = _resolve_api_base(model, step.get("api_base"))
    if url is None and model.split("/", 1)[0] == "ollama":
        url = _OLLAMA_DEFAULT_BASE
    return model, url


def legacy_endpoint_id(step: dict) -> str:
    """Stable ID for the endpoint a legacy ``model``/``api_base`` step resolves to."""
    model, url = _legacy_model_and_url(step)
    return f"legacy:{model}|{url or 'provider-default'}"


def legacy_step_endpoint(step: dict) -> Endpoint:
    """Endpoint for a legacy step: loopback URL -> ``local_llm``, else ``cloud_llm``.

    A model with no resolvable base (e.g. ``gpt-4o-mini``) is a hosted provider call.
    """
    model, url = _legacy_model_and_url(step)
    kind = EndpointKind.LOCAL_LLM if is_loopback_url(url) else EndpointKind.CLOUD_LLM
    return Endpoint(id=legacy_endpoint_id(step), kind=kind, model=model, url=url)
