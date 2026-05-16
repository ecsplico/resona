"""Engine catalogue, resolution, and dispatch for the resona-api gateway."""
import logging
import time
from threading import Lock
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from decouple import config

log = logging.getLogger(__name__)

# ── Cloud provider tables ────────────────────────────────────────────────
CLOUD_PROVIDERS = ("deepgram", "openai", "elevenlabs")
CLOUD_ENV_KEYS = {
    "deepgram": "DEEPGRAM_API_KEY",
    "openai": "OPENAI_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
}
CLOUD_STT_MODELS = {
    "deepgram": "nova-3",
    "openai": "whisper-1",
    "elevenlabs": "scribe_v1",
}

_CACHE_TTL = 5.0


# ── Errors ───────────────────────────────────────────────────────────────
class EngineError(Exception):
    """Base class for engine resolution errors."""


class EngineNotFoundError(EngineError):
    """Requested engine name is not in the catalogue."""


class EngineUnavailableError(EngineError):
    """Engine exists in the catalogue but is not currently available."""


class CapabilityError(EngineError):
    """Engine does not support the requested capability."""


class PrivacyViolationError(EngineError):
    """A non-private engine was requested under private=true."""


class NoEngineError(EngineError):
    """No engine satisfies the request."""


# ── Catalogue ────────────────────────────────────────────────────────────
@dataclass
class EngineInfo:
    """One transcription/synthesis engine known to the gateway."""

    name: str
    kind: str                       # "local" | "cloud"
    capabilities: list[str]         # subset of ["stt", "tts"]
    private: bool
    available: bool
    models: list[str] = field(default_factory=list)
    url: str | None = None          # local only
    provider: str | None = None     # cloud only


def _engine_urls() -> list[str]:
    """Parse RESONA_ENGINE_URLS into a list of trimmed base URLs."""
    raw = config("RESONA_ENGINE_URLS", default="http://localhost:7001")
    return [u.strip().rstrip("/") for u in raw.split(",") if u.strip()]


def _probe_local(url: str) -> EngineInfo:
    """Probe one engine-server /health endpoint; build its EngineInfo."""
    try:
        resp = httpx.get(f"{url}/health", timeout=5.0)
        resp.raise_for_status()
        body = resp.json()
        return EngineInfo(
            name=body.get("engine") or url,
            kind="local",
            capabilities=["stt"],
            private=True,
            available=True,
            models=list(body.get("models") or []),
            url=url,
        )
    except Exception as exc:
        log.warning("engine-server at %s unreachable: %s", url, exc)
        return EngineInfo(
            name=url, kind="local", capabilities=["stt"],
            private=True, available=False, models=[], url=url,
        )


def _cloud_engines() -> list[EngineInfo]:
    """Build a cloud EngineInfo per provider; available iff its key is set."""
    out: list[EngineInfo] = []
    for name in CLOUD_PROVIDERS:
        key = config(CLOUD_ENV_KEYS[name], default="")
        out.append(EngineInfo(
            name=name,
            kind="cloud",
            capabilities=["stt", "tts"],
            private=False,
            available=bool(key),
            models=[CLOUD_STT_MODELS[name]],
            provider=name,
        ))
    return out


def build_catalogue() -> list[EngineInfo]:
    """Probe every local backend + cloud provider; dedupe local engine names."""
    catalogue: list[EngineInfo] = []
    seen: dict[str, str] = {}
    for url in _engine_urls():
        info = _probe_local(url)
        if info.available and info.name in seen:
            log.warning(
                "duplicate local engine '%s' at %s (already at %s) — skipping",
                info.name, url, seen[info.name],
            )
            continue
        if info.available:
            seen[info.name] = url
        catalogue.append(info)
    catalogue.extend(_cloud_engines())
    return catalogue


_cache: tuple[float, list[EngineInfo]] | None = None
_cache_lock = Lock()


def get_catalogue(fresh: bool = False) -> list[EngineInfo]:
    """Return the engine catalogue, cached for a few seconds unless ``fresh``."""
    global _cache
    now = time.monotonic()
    if not fresh and _cache is not None and now - _cache[0] < _CACHE_TTL:
        return _cache[1]
    with _cache_lock:
        if not fresh and _cache is not None and now - _cache[0] < _CACHE_TTL:
            return _cache[1]
        catalogue = build_catalogue()
        _cache = (now, catalogue)
        return catalogue


def default_engine_name() -> str | None:
    """The configured RESONA_DEFAULT_ENGINE, or None if unset."""
    return config("RESONA_DEFAULT_ENGINE", default="") or None


def resolve(
    engine: str | None,
    capability: str,
    private: bool,
    catalogue: list[EngineInfo] | None = None,
) -> EngineInfo:
    """Resolve a request to a concrete engine.

    Args:
        engine: explicit engine name, ``"local"`` alias, or None for default.
        capability: ``"stt"`` or ``"tts"``.
        private: when True, only private (local) engines are eligible.
        catalogue: override the live catalogue (tests).

    Raises:
        EngineNotFoundError, EngineUnavailableError, CapabilityError,
        PrivacyViolationError, NoEngineError.
    """
    cat = catalogue if catalogue is not None else get_catalogue()

    local_only = engine == "local"
    if local_only:
        engine = None

    if engine:
        match = next((e for e in cat if e.name == engine), None)
        if match is None:
            raise EngineNotFoundError(f"unknown engine '{engine}'")
        if private and not match.private:
            raise PrivacyViolationError(
                f"engine '{engine}' is not private — refused under private=true"
            )
        if capability not in match.capabilities:
            raise CapabilityError(
                f"engine '{engine}' does not support {capability}"
            )
        if not match.available:
            raise EngineUnavailableError(f"engine '{engine}' is not available")
        return match

    candidates = [
        e for e in cat if e.available and capability in e.capabilities
    ]
    if private:
        candidates = [e for e in candidates if e.private]
    if local_only:
        candidates = [e for e in candidates if e.kind == "local"]
    if not candidates:
        what = "private " if private else ""
        raise NoEngineError(f"no {what}engine available for {capability}")

    default = default_engine_name()
    if default:
        for e in candidates:
            if e.name == default:
                return e
    for e in candidates:
        if e.kind == "local":
            return e
    return candidates[0]


def effective_default(
    capability: str = "stt", catalogue: list[EngineInfo] | None = None
) -> str | None:
    """The engine name a no-``engine`` request would resolve to, or None."""
    try:
        return resolve(None, capability, False, catalogue=catalogue).name
    except EngineError:
        return None


# ── Dispatch ─────────────────────────────────────────────────────────────
_clients: dict[str, object] = {}


def _engine_client(url: str):
    """Return a pooled EngineClient for ``url`` (created on first use)."""
    from .engine_client import EngineClient
    if url not in _clients:
        _clients[url] = EngineClient(base_url=url)
    return _clients[url]


def _cloud_key(provider: str, error_cls) -> str:
    """Resolve a cloud provider's API key from env, or raise ``error_cls``."""
    env_var = CLOUD_ENV_KEYS[provider]
    key = config(env_var, default="")
    if not key:
        raise error_cls(env_var)
    return key


def run_stt(
    info: EngineInfo,
    audio_path: Path,
    *,
    language: str = "de",
    model: str | None = None,
    prompt: str = "",
    task: str = "transcribe",
) -> dict:
    """Dispatch an STT request; return ``{text, language, segments}``."""
    if info.kind == "local":
        return _engine_client(info.url).transcribe(
            filepath=audio_path,
            language=language,
            initial_prompt=prompt,
            task=task,
        )
    from resona_cloud_stt.errors import MissingAPIKeyError
    from resona_cloud_stt.registry import get_provider
    key = _cloud_key(info.provider, MissingAPIKeyError)
    provider = get_provider(info.provider)
    return provider.transcribe(
        audio_path, api_key=key, model=model, language=language
    )


def run_tts(
    info: EngineInfo,
    text: str,
    *,
    model: str | None = None,
    voice: str | None = None,
    response_format: str = "mp3",
    speed: float | None = None,
) -> dict:
    """Dispatch a TTS request to a cloud engine; return a SpeechResult dict."""
    from resona_cloud_tts.errors import MissingAPIKeyError
    from resona_cloud_tts.registry import get_provider
    key = _cloud_key(info.provider, MissingAPIKeyError)
    provider = get_provider(info.provider)
    options = {"speed": speed} if speed is not None else None
    return provider.synthesize(
        text,
        api_key=key,
        model=model,
        voice=voice,
        response_format=response_format,
        options=options,
    )
