"""Engine catalogue, resolution, and dispatch for the resona-api gateway."""
import logging
import time
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


def get_catalogue(fresh: bool = False) -> list[EngineInfo]:
    """Return the engine catalogue, cached for a few seconds unless ``fresh``."""
    global _cache
    now = time.monotonic()
    if not fresh and _cache is not None and now - _cache[0] < _CACHE_TTL:
        return _cache[1]
    catalogue = build_catalogue()
    _cache = (now, catalogue)
    return catalogue


def default_engine_name() -> str | None:
    """The configured RESONA_DEFAULT_ENGINE, or None if unset."""
    return config("RESONA_DEFAULT_ENGINE", default="") or None
