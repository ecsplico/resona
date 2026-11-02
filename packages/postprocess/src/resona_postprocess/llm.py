"""LLM-based postprocessing via litellm.

Every call names an approved :class:`Endpoint` and an :class:`ExecutionPolicy`.
The policy is re-checked on each call, immediately before litellm is reached.
Nothing here falls back to an ambient model or base URL; the endpoint fixes both.
"""

import json as _json
import logging
import re

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

from .chunking import length_deviation, markup_signature, split_markdown

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 120.0

_UNSET = object()
# Resolved lazily on first use (keeps the heavy `import litellm` out of CLI
# startup). ``_UNSET`` = not yet attempted; ``None`` = attempted/unavailable.
# Exposed at module scope so tests can monkeypatch it directly.
litellm = _UNSET


class LLMUnavailableError(RuntimeError):
    """Raised when an LLM step is requested but litellm is not installed."""


class PostprocessStepFailed(RuntimeError):
    """A step that must not be skipped failed; the pipeline re-raises it.

    ``reason`` is content-free (chunk number, attempt count), safe to log and report.
    """

    def __init__(self, step: str, reason: str) -> None:
        super().__init__(f"{step}: {reason}")
        self.step = step
        self.reason = reason


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
        import os
        # litellm otherwise downloads its model price map from GitHub at import time;
        # a private worker only talks to its approved endpoints.
        os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
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


# Sent as the API key to ``local_llm`` endpoints. OpenAI-compatible local servers
# (llama-swap, llama.cpp, LM Studio) ignore it, but litellm's ``openai/`` provider
# refuses to call without a key and would otherwise pick up an ambient
# OPENAI_API_KEY and send that cloud credential to the local server.
_LOCAL_API_KEY = "resona-local"


def _api_key_for(endpoint: Endpoint) -> str | None:
    return _LOCAL_API_KEY if endpoint.kind == EndpointKind.LOCAL_LLM else None


def _completion(*, model, api_base, messages, temperature, max_tokens,
                response_format=None, timeout=None, attempts=2, api_key=None):
    """Call litellm.completion, retrying a transient failure up to ``attempts`` calls in total."""
    import os
    os.environ.setdefault("LITELLM_LOG", "ERROR")
    litellm = _resolve_litellm()
    kwargs = {
        "model": model,
        "api_base": api_base,
        "messages": messages,
        "timeout": timeout or _DEFAULT_TIMEOUT,
    }
    if api_key is not None:
        kwargs["api_key"] = api_key
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if response_format is not None:
        kwargs["response_format"] = response_format

    last_exc = None
    for attempt in range(1, attempts + 1):
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
    timeout: float | None = None,
    attempts: int = 2,
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
        timeout=timeout,
        attempts=attempts,
        api_key=_api_key_for(endpoint),
    )


_THINK = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)
_FENCES = ("```", "~~~")


def _clean_reply(reply: str | None, source: str) -> str:
    """Strip a leading reasoning block and a code fence wrapped around the whole reply."""
    text = reply or ""
    if not source.startswith("<think>"):
        text = _THINK.sub("", text, count=1)
    text = text.strip()
    if text.startswith(_FENCES) and not source.startswith(_FENCES):
        lines = text.splitlines()
        if len(lines) >= 2 and lines[-1].strip() in _FENCES:
            text = "\n".join(lines[1:-1]).strip()
    return text


def llm_transform_chunked(
    text: str,
    *,
    prompt: str,
    endpoint: Endpoint,
    policy: ExecutionPolicy,
    chunk_chars: int,
    step_name: str = "llm",
    max_attempts: int = 3,
    max_length_deviation: float | None = None,
    preserve_markup: bool = False,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
) -> tuple[str, list[str]]:
    """Transform long Markdown chunk by chunk (see :func:`split_markdown`).

    Each chunk gets at most ``max_attempts`` calls; a chunk that keeps failing raises
    :class:`PostprocessStepFailed` (never a silent skip). A reply whose length deviates
    from its chunk by more than ``max_length_deviation`` (relative), or that changes the
    chunk's placeholders/headings/table shape when ``preserve_markup`` is set, is
    rejected without retry: the raw chunk is kept and
    ``polish_chunk_rejected:<n>:<length|markup>`` is returned as a warning.
    ``PolicyDenied`` propagates unchanged.
    """
    chunks = split_markdown(text, chunk_chars)
    out: list[str] = []
    warnings: list[str] = []
    for number, chunk in enumerate(chunks, start=1):
        core = chunk.strip()
        if not core:
            out.append(chunk)
            continue
        reply = None
        for attempt in range(1, max_attempts + 1):
            try:
                reply = llm_transform(
                    core, prompt=prompt, endpoint=endpoint, policy=policy,
                    temperature=temperature, max_tokens=max_tokens, timeout=timeout, attempts=1,
                )
                break
            except PolicyDenied:
                raise
            except LLMUnavailableError:
                raise PostprocessStepFailed(step_name, "litellm is not installed") from None
            except Exception as e:  # noqa: BLE001 — litellm raises many error types
                log.warning("Step %r chunk %d/%d attempt %d/%d failed: %s",
                            step_name, number, len(chunks), attempt, max_attempts, type(e).__name__)
        else:
            raise PostprocessStepFailed(step_name, f"chunk {number} failed after {max_attempts} attempts")

        polished = _clean_reply(reply, core)
        reason = None
        if max_length_deviation is not None and length_deviation(core, polished) > max_length_deviation:
            reason = "length"
        elif preserve_markup and markup_signature(polished) != markup_signature(core):
            reason = "markup"
        if reason is not None:
            log.warning("Step %r chunk %d/%d rejected (%s); raw chunk kept", step_name, number, len(chunks), reason)
            warnings.append(f"polish_chunk_rejected:{number}:{reason}")
            out.append(chunk)
            continue
        lead = chunk[: len(chunk) - len(chunk.lstrip())]
        trail = chunk[len(chunk.rstrip()):]
        out.append(f"{lead}{polished}{trail}")
    return "".join(out), warnings


def llm_extract(
    text: str,
    *,
    prompt: str,
    endpoint: Endpoint,
    policy: ExecutionPolicy,
    temperature: float | None = None,
    timeout: float | None = None,
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
        timeout=timeout,
        api_key=_api_key_for(endpoint),
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
