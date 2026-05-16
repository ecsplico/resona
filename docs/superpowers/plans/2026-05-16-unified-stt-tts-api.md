# Unified STT/TTS API + Engine Discovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `resona-api` into an OpenAI-compatible `/v1/audio/*` gateway that discovers and routes to multiple local `engine-server` backends plus cloud STT/TTS providers, with a `/v1/engines` discovery route and a `private` routing guard.

**Architecture:** A new `resona-cloud-tts` package mirrors `resona-cloud-stt` for text-to-speech. A new `engine_registry` module in `resona-api` builds an engine catalogue (probing each local backend's `/health`, checking cloud API-key env vars), resolves a request to a concrete engine, and dispatches STT/TTS calls. New routes in `audio_routes.py` expose the OpenAI-compatible API. The async `/jobs` queue is rewired to route through the same registry.

**Tech Stack:** Python 3.12, FastAPI, httpx, SQLModel/SQLite, pytest + respx, uv workspace.

**Spec:** `docs/superpowers/specs/2026-05-16-unified-stt-tts-api-design.md`

---

## File structure

**New package `packages/cloud-tts/`:**
- `pyproject.toml` — workspace member, deps `httpx`
- `src/resona_cloud_tts/__init__.py` — public exports
- `src/resona_cloud_tts/types.py` — `SpeechResult` TypedDict
- `src/resona_cloud_tts/errors.py` — `CloudTTSError`, `MissingAPIKeyError`, `ProviderHTTPError`
- `src/resona_cloud_tts/registry.py` — provider tables + `get_provider()`
- `src/resona_cloud_tts/providers/{openai,elevenlabs,deepgram}.py` — `synthesize()`
- `tests/conftest.py`, `tests/test_{openai,elevenlabs,deepgram,registry}.py`

**Modified `packages/engine-server/`:**
- `src/resona_engine_server/app.py` — `/health` reports `{status, engine, models}`
- `tests/test_app.py` — updated `/health` assertion

**New / modified in `packages/api/src/resona_api/`:**
- `engine_registry.py` — NEW: catalogue, resolution, dispatch
- `audio_routes.py` — NEW: `/v1/audio/*` + `/v1/engines` router
- `app.py` — include the new router, drop the single `EngineClient`
- `tasks_transcribe.py` — route through `engine_registry`
- `endpoints.py` — `POST /jobs` gains an optional `engine` field
- `db/models.py` — `Job` gains `engine` column
- `db/engine.py` — idempotent `engine` column migration
- `db/utils.py` — `register_job()` gains `engine` param
- `pyproject.toml` — add `resona-cloud-tts` dependency
- `tests/conftest.py` — `test_app` includes the audio router

**Modified:** `docker-compose.resona.yml`, `CLAUDE.md`, `README.md`.

---

## Task 1: Scaffold the `resona-cloud-tts` package

**Files:**
- Create: `packages/cloud-tts/pyproject.toml`
- Create: `packages/cloud-tts/src/resona_cloud_tts/__init__.py`
- Create: `packages/cloud-tts/src/resona_cloud_tts/types.py`
- Create: `packages/cloud-tts/src/resona_cloud_tts/errors.py`
- Create: `packages/cloud-tts/src/resona_cloud_tts/registry.py`
- Create: `packages/cloud-tts/src/resona_cloud_tts/providers/__init__.py`
- Create: `packages/cloud-tts/tests/test_registry.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "resona-cloud-tts"
version = "0.1.0"
description = "Cloud text-to-speech providers (OpenAI, ElevenLabs, Deepgram) for Resona"
requires-python = ">=3.12"
dependencies = [
    "httpx>=0.28.1",
]

[tool.hatch.build.targets.wheel]
packages = ["src/resona_cloud_tts"]
```

- [ ] **Step 2: Create `types.py`**

```python
"""Normalized result type shared by every cloud TTS provider."""
from typing import TypedDict


class SpeechResult(TypedDict):
    """Return type for all cloud provider synthesize() functions.

    ``audio`` is the raw encoded audio; ``content_type`` is its MIME type.
    """
    audio: bytes
    content_type: str
```

- [ ] **Step 3: Create `errors.py`**

```python
"""Exception hierarchy for resona-cloud-tts."""


class CloudTTSError(Exception):
    """Base class for all cloud-tts errors."""


class MissingAPIKeyError(CloudTTSError):
    """Raised when the provider's API key env var is not set.

    Attributes:
        env_var: Name of the missing environment variable.
    """

    def __init__(self, env_var: str) -> None:
        self.env_var = env_var
        super().__init__(
            f"Missing API key — set the {env_var} environment variable."
        )


class ProviderHTTPError(CloudTTSError):
    """Raised when a provider returns a non-2xx HTTP response.

    Attributes:
        provider: Provider name (``openai``/``elevenlabs``/``deepgram``).
        status_code: HTTP status code returned.
        body: Response body text (provider error message).
    """

    def __init__(self, provider: str, status_code: int, body: str) -> None:
        self.provider = provider
        self.status_code = status_code
        self.body = body
        super().__init__(f"{provider} returned HTTP {status_code}: {body}")
```

- [ ] **Step 4: Create `registry.py`**

```python
"""Provider registry — names, env keys, default models/voices, lookup."""
import importlib
from types import ModuleType

PROVIDERS: set[str] = {"openai", "elevenlabs", "deepgram"}

PROVIDER_ENV_KEYS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "deepgram": "DEEPGRAM_API_KEY",
}

DEFAULT_MODELS: dict[str, str] = {
    "openai": "tts-1",
    "elevenlabs": "eleven_multilingual_v2",
    "deepgram": "aura-2-thalia-en",
}

DEFAULT_VOICES: dict[str, str | None] = {
    "openai": "alloy",
    "elevenlabs": "21m00Tcm4TlvDq8ikWAM",  # ElevenLabs "Rachel" voice id
    "deepgram": None,                       # voice is encoded in the model
}

# MIME type for each supported response_format.
CONTENT_TYPES: dict[str, str] = {
    "mp3": "audio/mpeg",
    "opus": "audio/opus",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "wav": "audio/wav",
}


def get_provider(name: str) -> ModuleType:
    """Return the provider module for ``name`` (has a ``synthesize`` function).

    Raises:
        ValueError: if ``name`` is not a known provider.
    """
    if name not in PROVIDERS:
        raise ValueError(f"Unknown provider '{name}'. Known: {sorted(PROVIDERS)}")
    return importlib.import_module(f"resona_cloud_tts.providers.{name}")
```

- [ ] **Step 5: Create `providers/__init__.py`** (empty file)

```python
```

- [ ] **Step 6: Create `__init__.py`**

```python
"""resona-cloud-tts — cloud text-to-speech provider wrappers for Resona."""
from .errors import CloudTTSError, MissingAPIKeyError, ProviderHTTPError
from .registry import (
    CONTENT_TYPES,
    DEFAULT_MODELS,
    DEFAULT_VOICES,
    PROVIDER_ENV_KEYS,
    PROVIDERS,
    get_provider,
)
from .types import SpeechResult

__all__ = [
    "SpeechResult",
    "CloudTTSError",
    "MissingAPIKeyError",
    "ProviderHTTPError",
    "PROVIDERS",
    "PROVIDER_ENV_KEYS",
    "DEFAULT_MODELS",
    "DEFAULT_VOICES",
    "CONTENT_TYPES",
    "get_provider",
]
```

- [ ] **Step 7: Create `tests/test_registry.py`**

```python
"""Tests for the resona-cloud-tts registry."""
import pytest

from resona_cloud_tts.registry import (
    DEFAULT_MODELS,
    PROVIDER_ENV_KEYS,
    PROVIDERS,
    get_provider,
)


def test_providers_have_env_keys_and_models():
    for name in PROVIDERS:
        assert name in PROVIDER_ENV_KEYS
        assert name in DEFAULT_MODELS


def test_get_provider_returns_module_with_synthesize():
    mod = get_provider("openai")
    assert hasattr(mod, "synthesize")


def test_get_provider_rejects_unknown():
    with pytest.raises(ValueError):
        get_provider("nope")
```

- [ ] **Step 8: Register the package in the workspace and verify**

The workspace root `pyproject.toml` already globs `packages/*`, so no edit is needed there. Sync and run the registry tests:

Run: `uv sync --all-packages --no-build-isolation-package openai-whisper`
Then: `uv run pytest packages/cloud-tts/tests/test_registry.py -v`
Expected: 3 tests PASS.

- [ ] **Step 9: Commit**

```bash
git add packages/cloud-tts/ uv.lock
git commit -m "feat(cloud-tts): scaffold resona-cloud-tts package with registry"
```

---

## Task 2: OpenAI TTS provider

**Files:**
- Create: `packages/cloud-tts/tests/conftest.py`
- Create: `packages/cloud-tts/src/resona_cloud_tts/providers/openai.py`
- Create: `packages/cloud-tts/tests/test_openai.py`

- [ ] **Step 1: Create `tests/conftest.py`**

```python
"""Shared fixtures for resona-cloud-tts tests."""
import pytest


@pytest.fixture
def fake_audio() -> bytes:
    """Stand-in for an encoded audio response body."""
    return b"ID3\x03\x00\x00\x00fake-mp3-bytes"
```

- [ ] **Step 2: Write the failing test `tests/test_openai.py`**

```python
"""Tests for the OpenAI cloud TTS provider."""
import httpx
import pytest
import respx

from resona_cloud_tts.errors import ProviderHTTPError
from resona_cloud_tts.providers import openai

URL = "https://api.openai.com/v1/audio/speech"


@respx.mock
def test_synthesize_returns_audio_and_content_type(fake_audio):
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, content=fake_audio)
    )
    result = openai.synthesize("hallo welt", api_key="oakey")
    assert route.called
    assert result["audio"] == fake_audio
    assert result["content_type"] == "audio/mpeg"


@respx.mock
def test_synthesize_sends_bearer_auth_and_json_body(fake_audio):
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, content=fake_audio)
    )
    openai.synthesize(
        "hallo", api_key="oakey", model="tts-1", voice="echo",
        response_format="opus",
    )
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer oakey"
    import json
    body = json.loads(req.content)
    assert body["model"] == "tts-1"
    assert body["voice"] == "echo"
    assert body["input"] == "hallo"
    assert body["response_format"] == "opus"


@respx.mock
def test_synthesize_drops_unknown_options(fake_audio, caplog):
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, content=fake_audio)
    )
    openai.synthesize(
        "hi", api_key="oakey", options={"speed": 1.2, "bogus": "x"}
    )
    import json
    body = json.loads(route.calls.last.request.content)
    assert body["speed"] == 1.2
    assert "bogus" not in body
    assert "bogus" in caplog.text


@respx.mock
def test_synthesize_raises_on_401():
    respx.post(URL).mock(return_value=httpx.Response(401, text="bad key"))
    with pytest.raises(ProviderHTTPError) as exc:
        openai.synthesize("hi", api_key="bad")
    assert exc.value.status_code == 401
    assert exc.value.provider == "openai"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest packages/cloud-tts/tests/test_openai.py -v`
Expected: FAIL — `ModuleNotFoundError: resona_cloud_tts.providers.openai`.

- [ ] **Step 4: Create `providers/openai.py`**

```python
"""OpenAI provider — JSON POST to /v1/audio/speech."""
import logging

import httpx

from ..errors import ProviderHTTPError
from ..registry import CONTENT_TYPES, DEFAULT_MODELS, DEFAULT_VOICES
from ..types import SpeechResult

log = logging.getLogger(__name__)

_URL = "https://api.openai.com/v1/audio/speech"
_TIMEOUT = 600.0
_OPTION_KEYS = {"speed"}


def _filter_options(options: dict | None) -> dict:
    """Keep only whitelisted keys; warn and drop the rest."""
    if not options:
        return {}
    kept: dict = {}
    for key, value in options.items():
        if key in _OPTION_KEYS:
            kept[key] = value
        else:
            log.warning("openai: dropping unknown option '%s'", key)
    return kept


def synthesize(
    text: str,
    *,
    api_key: str,
    model: str | None = None,
    voice: str | None = None,
    response_format: str = "mp3",
    options: dict | None = None,
) -> SpeechResult:
    """Synthesize ``text`` to speech via the OpenAI audio speech API."""
    if response_format not in CONTENT_TYPES:
        from ..errors import CloudTTSError
        raise CloudTTSError(f"openai: unsupported response_format '{response_format}'")

    body: dict = {
        "model": model or DEFAULT_MODELS["openai"],
        "input": text,
        "voice": voice or DEFAULT_VOICES["openai"],
        "response_format": response_format,
    }
    body.update(_filter_options(options))

    resp = httpx.post(
        _URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json=body,
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 300:
        raise ProviderHTTPError("openai", resp.status_code, resp.text)

    return SpeechResult(
        audio=resp.content,
        content_type=CONTENT_TYPES[response_format],
    )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest packages/cloud-tts/tests/test_openai.py -v`
Expected: 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/cloud-tts/src/resona_cloud_tts/providers/openai.py packages/cloud-tts/tests/
git commit -m "feat(cloud-tts): add OpenAI text-to-speech provider"
```

---

## Task 3: ElevenLabs TTS provider

**Files:**
- Create: `packages/cloud-tts/src/resona_cloud_tts/providers/elevenlabs.py`
- Create: `packages/cloud-tts/tests/test_elevenlabs.py`

- [ ] **Step 1: Write the failing test `tests/test_elevenlabs.py`**

```python
"""Tests for the ElevenLabs cloud TTS provider."""
import httpx
import pytest
import respx

from resona_cloud_tts.errors import CloudTTSError, ProviderHTTPError
from resona_cloud_tts.providers import elevenlabs

VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
URL = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"


@respx.mock
def test_synthesize_posts_to_voice_path_with_api_key(fake_audio):
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, content=fake_audio)
    )
    result = elevenlabs.synthesize("hallo", api_key="elkey")
    assert route.called
    req = route.calls.last.request
    assert req.headers["xi-api-key"] == "elkey"
    assert result["audio"] == fake_audio
    assert result["content_type"] == "audio/mpeg"


@respx.mock
def test_synthesize_uses_explicit_voice_and_model(fake_audio):
    voice = "customVoice123"
    route = respx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}"
    ).mock(return_value=httpx.Response(200, content=fake_audio))
    elevenlabs.synthesize(
        "hallo", api_key="elkey", voice=voice, model="eleven_turbo_v2"
    )
    import json
    body = json.loads(route.calls.last.request.content)
    assert body["text"] == "hallo"
    assert body["model_id"] == "eleven_turbo_v2"


@respx.mock
def test_synthesize_folds_voice_settings_options(fake_audio):
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, content=fake_audio)
    )
    elevenlabs.synthesize(
        "hi", api_key="elkey",
        options={"stability": 0.7, "bogus": 1},
    )
    import json
    body = json.loads(route.calls.last.request.content)
    assert body["voice_settings"]["stability"] == 0.7
    assert "bogus" not in str(body)


def test_synthesize_rejects_unsupported_format():
    with pytest.raises(CloudTTSError):
        elevenlabs.synthesize("hi", api_key="elkey", response_format="flac")


@respx.mock
def test_synthesize_raises_on_401():
    respx.post(URL).mock(return_value=httpx.Response(401, text="bad key"))
    with pytest.raises(ProviderHTTPError) as exc:
        elevenlabs.synthesize("hi", api_key="bad")
    assert exc.value.provider == "elevenlabs"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest packages/cloud-tts/tests/test_elevenlabs.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create `providers/elevenlabs.py`**

```python
"""ElevenLabs provider — JSON POST to /v1/text-to-speech/{voice_id}."""
import logging

import httpx

from ..errors import CloudTTSError, ProviderHTTPError
from ..registry import CONTENT_TYPES, DEFAULT_MODELS, DEFAULT_VOICES
from ..types import SpeechResult

log = logging.getLogger(__name__)

_BASE = "https://api.elevenlabs.io/v1/text-to-speech"
_TIMEOUT = 600.0
_OPTION_KEYS = {"stability", "similarity_boost", "style"}

# ElevenLabs only supports a subset of formats; map to its output_format ids.
_OUTPUT_FORMATS = {
    "mp3": "mp3_44100_128",
    "opus": "opus_48000_128",
}


def _filter_options(options: dict | None) -> dict:
    """Keep only whitelisted voice_settings keys; warn and drop the rest."""
    if not options:
        return {}
    kept: dict = {}
    for key, value in options.items():
        if key in _OPTION_KEYS:
            kept[key] = value
        else:
            log.warning("elevenlabs: dropping unknown option '%s'", key)
    return kept


def synthesize(
    text: str,
    *,
    api_key: str,
    model: str | None = None,
    voice: str | None = None,
    response_format: str = "mp3",
    options: dict | None = None,
) -> SpeechResult:
    """Synthesize ``text`` to speech via the ElevenLabs text-to-speech API."""
    if response_format not in _OUTPUT_FORMATS:
        raise CloudTTSError(
            f"elevenlabs: unsupported response_format '{response_format}' "
            f"(supported: {sorted(_OUTPUT_FORMATS)})"
        )

    voice_id = voice or DEFAULT_VOICES["elevenlabs"]
    body: dict = {
        "text": text,
        "model_id": model or DEFAULT_MODELS["elevenlabs"],
    }
    voice_settings = _filter_options(options)
    if voice_settings:
        body["voice_settings"] = voice_settings

    resp = httpx.post(
        f"{_BASE}/{voice_id}",
        headers={"xi-api-key": api_key},
        params={"output_format": _OUTPUT_FORMATS[response_format]},
        json=body,
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 300:
        raise ProviderHTTPError("elevenlabs", resp.status_code, resp.text)

    return SpeechResult(
        audio=resp.content,
        content_type=CONTENT_TYPES[response_format],
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest packages/cloud-tts/tests/test_elevenlabs.py -v`
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/cloud-tts/src/resona_cloud_tts/providers/elevenlabs.py packages/cloud-tts/tests/test_elevenlabs.py
git commit -m "feat(cloud-tts): add ElevenLabs text-to-speech provider"
```

---

## Task 4: Deepgram TTS provider

**Files:**
- Create: `packages/cloud-tts/src/resona_cloud_tts/providers/deepgram.py`
- Create: `packages/cloud-tts/tests/test_deepgram.py`

- [ ] **Step 1: Write the failing test `tests/test_deepgram.py`**

```python
"""Tests for the Deepgram cloud TTS provider."""
import httpx
import pytest
import respx

from resona_cloud_tts.errors import ProviderHTTPError
from resona_cloud_tts.providers import deepgram

URL = "https://api.deepgram.com/v1/speak"


@respx.mock
def test_synthesize_sends_token_auth_and_text_body(fake_audio):
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, content=fake_audio)
    )
    result = deepgram.synthesize("hallo", api_key="dgkey")
    assert route.called
    req = route.calls.last.request
    assert req.headers["authorization"] == "Token dgkey"
    import json
    assert json.loads(req.content)["text"] == "hallo"
    assert result["audio"] == fake_audio
    assert result["content_type"] == "audio/mpeg"


@respx.mock
def test_synthesize_passes_model_and_encoding_params(fake_audio):
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, content=fake_audio)
    )
    deepgram.synthesize(
        "hi", api_key="dgkey", model="aura-2-orpheus-en",
        response_format="opus",
    )
    params = route.calls.last.request.url.params
    assert params["model"] == "aura-2-orpheus-en"
    assert params["encoding"] == "opus"


@respx.mock
def test_synthesize_raises_on_400():
    respx.post(URL).mock(return_value=httpx.Response(400, text="bad request"))
    with pytest.raises(ProviderHTTPError) as exc:
        deepgram.synthesize("hi", api_key="dgkey")
    assert exc.value.provider == "deepgram"
    assert exc.value.status_code == 400
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest packages/cloud-tts/tests/test_deepgram.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create `providers/deepgram.py`**

```python
"""Deepgram provider — JSON POST to /v1/speak."""
import logging

import httpx

from ..errors import CloudTTSError, ProviderHTTPError
from ..registry import CONTENT_TYPES, DEFAULT_MODELS
from ..types import SpeechResult

log = logging.getLogger(__name__)

_URL = "https://api.deepgram.com/v1/speak"
_TIMEOUT = 600.0
_OPTION_KEYS = {"sample_rate"}

# response_format -> Deepgram ``encoding`` query value.
_ENCODINGS = {
    "mp3": "mp3",
    "opus": "opus",
    "flac": "flac",
    "aac": "aac",
    "wav": "linear16",
}


def _filter_options(options: dict | None) -> dict:
    """Keep only whitelisted query keys; warn and drop the rest."""
    if not options:
        return {}
    kept: dict = {}
    for key, value in options.items():
        if key in _OPTION_KEYS:
            kept[key] = value
        else:
            log.warning("deepgram: dropping unknown option '%s'", key)
    return kept


def synthesize(
    text: str,
    *,
    api_key: str,
    model: str | None = None,
    voice: str | None = None,
    response_format: str = "mp3",
    options: dict | None = None,
) -> SpeechResult:
    """Synthesize ``text`` to speech via the Deepgram speak API.

    Deepgram encodes the voice in the model name; an explicit ``voice``
    argument overrides ``model``.
    """
    if response_format not in _ENCODINGS:
        raise CloudTTSError(
            f"deepgram: unsupported response_format '{response_format}'"
        )

    params: dict = {
        "model": voice or model or DEFAULT_MODELS["deepgram"],
        "encoding": _ENCODINGS[response_format],
    }
    if response_format == "wav":
        params["container"] = "wav"
    params.update(_filter_options(options))

    resp = httpx.post(
        _URL,
        headers={"Authorization": f"Token {api_key}"},
        params=params,
        json={"text": text},
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 300:
        raise ProviderHTTPError("deepgram", resp.status_code, resp.text)

    return SpeechResult(
        audio=resp.content,
        content_type=CONTENT_TYPES[response_format],
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest packages/cloud-tts/tests/test_deepgram.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Run the whole cloud-tts suite**

Run: `uv run pytest packages/cloud-tts/tests/ -v`
Expected: all tests PASS (15 total).

- [ ] **Step 6: Commit**

```bash
git add packages/cloud-tts/src/resona_cloud_tts/providers/deepgram.py packages/cloud-tts/tests/test_deepgram.py
git commit -m "feat(cloud-tts): add Deepgram text-to-speech provider"
```

---

## Task 5: `engine-server` `/health` reports engine name and models

**Files:**
- Modify: `packages/engine-server/src/resona_engine_server/app.py:77-79`
- Modify: `packages/engine-server/tests/test_app.py:32-35`

- [ ] **Step 1: Update the failing test `test_app.py`**

Replace the existing `test_health` function (currently asserting `{"status": "ok"}`):

```python
def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "engine" in body
    assert isinstance(body["models"], list)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest packages/engine-server/tests/test_app.py::test_health -v`
Expected: FAIL — `KeyError: 'engine'`.

- [ ] **Step 3: Update `app.py` `/health`**

Replace the `health` function (lines 77-79):

```python
_MODEL_ENV = {
    "faster-whisper": ("DEFAULT_FASTWHISPER_MODEL", "large-v3"),
    "whisper": ("DEFAULT_WHISPER_MODEL", "large-v3"),
    "voxtral": ("DEFAULT_VOXTRAL_MODEL", "openai/whisper-large-v3"),
}


@app.get("/health")
async def health():
    """Report liveness plus which engine and model this process serves."""
    engine_name = config("RESONA_ENGINE", default="faster-whisper")
    env_key, default_model = _MODEL_ENV.get(
        engine_name, ("DEFAULT_FASTWHISPER_MODEL", "large-v3")
    )
    model = config(env_key, default=default_model)
    return {"status": "ok", "engine": engine_name, "models": [model]}
```

`config` is already imported at the top of `app.py`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest packages/engine-server/tests/test_app.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/engine-server/src/resona_engine_server/app.py packages/engine-server/tests/test_app.py
git commit -m "feat(engine-server): /health reports engine name and models"
```

---

## Task 6: `engine_registry` — catalogue construction

**Files:**
- Create: `packages/api/src/resona_api/engine_registry.py`
- Create: `packages/api/tests/test_engine_registry.py`

- [ ] **Step 1: Write the failing test `test_engine_registry.py`**

```python
"""Tests for the resona-api engine registry."""
import httpx
import respx

from resona_api import engine_registry as reg


@respx.mock
def test_build_catalogue_probes_local_and_lists_cloud(monkeypatch):
    monkeypatch.setenv("RESONA_ENGINE_URLS", "http://eng-a:7001")
    monkeypatch.setenv("OPENAI_API_KEY", "set")
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    respx.get("http://eng-a:7001/health").mock(
        return_value=httpx.Response(
            200, json={"status": "ok", "engine": "faster-whisper",
                       "models": ["large-v3"]}
        )
    )
    catalogue = reg.build_catalogue()
    by_name = {e.name: e for e in catalogue}

    assert by_name["faster-whisper"].kind == "local"
    assert by_name["faster-whisper"].private is True
    assert by_name["faster-whisper"].available is True
    assert by_name["faster-whisper"].capabilities == ["stt"]

    assert by_name["openai"].kind == "cloud"
    assert by_name["openai"].private is False
    assert by_name["openai"].available is True
    assert "tts" in by_name["openai"].capabilities

    assert by_name["deepgram"].available is False


@respx.mock
def test_unreachable_local_is_listed_unavailable(monkeypatch):
    monkeypatch.setenv("RESONA_ENGINE_URLS", "http://dead:7001")
    respx.get("http://dead:7001/health").mock(side_effect=httpx.ConnectError("x"))
    catalogue = reg.build_catalogue()
    local = [e for e in catalogue if e.kind == "local"]
    assert len(local) == 1
    assert local[0].available is False


@respx.mock
def test_duplicate_local_engine_name_is_deduped(monkeypatch):
    monkeypatch.setenv("RESONA_ENGINE_URLS", "http://a:7001,http://b:7001")
    body = {"status": "ok", "engine": "whisper", "models": ["large-v3"]}
    respx.get("http://a:7001/health").mock(return_value=httpx.Response(200, json=body))
    respx.get("http://b:7001/health").mock(return_value=httpx.Response(200, json=body))
    catalogue = reg.build_catalogue()
    local = [e for e in catalogue if e.kind == "local"]
    assert len(local) == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest packages/api/tests/test_engine_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: resona_api.engine_registry`.

- [ ] **Step 3: Create `engine_registry.py`** (catalogue half)

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest packages/api/tests/test_engine_registry.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/api/src/resona_api/engine_registry.py packages/api/tests/test_engine_registry.py
git commit -m "feat(api): engine_registry catalogue construction"
```

---

## Task 7: `engine_registry` — `resolve()`

**Files:**
- Modify: `packages/api/src/resona_api/engine_registry.py` (append)
- Modify: `packages/api/tests/test_engine_registry.py` (append)

- [ ] **Step 1: Append failing tests to `test_engine_registry.py`**

```python
def _cat():
    """A fixed catalogue for resolve() tests."""
    return [
        reg.EngineInfo("faster-whisper", "local", ["stt"], True, True, ["large-v3"]),
        reg.EngineInfo("whisper", "local", ["stt"], True, False, []),
        reg.EngineInfo("deepgram", "cloud", ["stt", "tts"], False, True,
                       ["nova-3"], provider="deepgram"),
    ]


def test_resolve_explicit_engine():
    info = reg.resolve("deepgram", "stt", False, catalogue=_cat())
    assert info.name == "deepgram"


def test_resolve_unknown_engine_raises():
    import pytest
    with pytest.raises(reg.EngineNotFoundError):
        reg.resolve("nope", "stt", False, catalogue=_cat())


def test_resolve_unavailable_engine_raises():
    import pytest
    with pytest.raises(reg.EngineUnavailableError):
        reg.resolve("whisper", "stt", False, catalogue=_cat())


def test_resolve_capability_mismatch_raises():
    import pytest
    with pytest.raises(reg.CapabilityError):
        reg.resolve("faster-whisper", "tts", False, catalogue=_cat())


def test_resolve_private_refuses_cloud_engine():
    import pytest
    with pytest.raises(reg.PrivacyViolationError):
        reg.resolve("deepgram", "stt", True, catalogue=_cat())


def test_resolve_default_prefers_local(monkeypatch):
    monkeypatch.delenv("RESONA_DEFAULT_ENGINE", raising=False)
    info = reg.resolve(None, "stt", False, catalogue=_cat())
    assert info.name == "faster-whisper"


def test_resolve_default_honours_env(monkeypatch):
    monkeypatch.setenv("RESONA_DEFAULT_ENGINE", "deepgram")
    info = reg.resolve(None, "stt", False, catalogue=_cat())
    assert info.name == "deepgram"


def test_resolve_no_private_engine_for_tts_raises():
    import pytest
    with pytest.raises(reg.NoEngineError):
        reg.resolve(None, "tts", True, catalogue=_cat())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/api/tests/test_engine_registry.py -k resolve -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'resolve'`.

- [ ] **Step 3: Append `resolve()` to `engine_registry.py`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest packages/api/tests/test_engine_registry.py -v`
Expected: all tests PASS (11 total).

- [ ] **Step 5: Commit**

```bash
git add packages/api/src/resona_api/engine_registry.py packages/api/tests/test_engine_registry.py
git commit -m "feat(api): engine_registry resolve() with private guard"
```

---

## Task 8: `engine_registry` — STT/TTS dispatch

**Files:**
- Modify: `packages/api/pyproject.toml` (add `resona-cloud-tts` dep)
- Modify: `packages/api/src/resona_api/engine_registry.py` (append)
- Modify: `packages/api/tests/test_engine_registry.py` (append)

- [ ] **Step 1: Add `resona-cloud-tts` to `packages/api/pyproject.toml`**

In `[project].dependencies`, add `"resona-cloud-tts",` after `"resona-cloud-stt",`.
In `[tool.uv.sources]`, add `resona-cloud-tts = { workspace = true }`.

Run: `uv sync --all-packages --no-build-isolation-package openai-whisper`
Expected: resolves cleanly.

- [ ] **Step 2: Append failing dispatch tests to `test_engine_registry.py`**

```python
@respx.mock
def test_run_stt_local_calls_engine_server(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFFfake")
    respx.post("http://eng:7001/transcribe").mock(
        return_value=httpx.Response(
            200, json={"text": "hallo", "language": "de", "segments": []}
        )
    )
    info = reg.EngineInfo("faster-whisper", "local", ["stt"], True, True,
                          [], url="http://eng:7001")
    result = reg.run_stt(info, audio, language="de")
    assert result["text"] == "hallo"


def test_run_stt_cloud_dispatches_to_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dgkey")
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFFfake")
    captured = {}

    class FakeProvider:
        @staticmethod
        def transcribe(path, *, api_key, model=None, language=None):
            captured["api_key"] = api_key
            return {"text": "cloud", "language": "de", "segments": []}

    monkeypatch.setattr(
        "resona_cloud_stt.registry.get_provider", lambda n: FakeProvider
    )
    info = reg.EngineInfo("deepgram", "cloud", ["stt", "tts"], False, True,
                          [], provider="deepgram")
    result = reg.run_stt(info, audio, language="de")
    assert result["text"] == "cloud"
    assert captured["api_key"] == "dgkey"


def test_run_tts_cloud_dispatches_to_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "oakey")

    class FakeProvider:
        @staticmethod
        def synthesize(text, *, api_key, model=None, voice=None,
                       response_format="mp3", options=None):
            return {"audio": b"sound", "content_type": "audio/mpeg"}

    monkeypatch.setattr(
        "resona_cloud_tts.registry.get_provider", lambda n: FakeProvider
    )
    info = reg.EngineInfo("openai", "cloud", ["stt", "tts"], False, True,
                          [], provider="openai")
    result = reg.run_tts(info, "hallo")
    assert result["audio"] == b"sound"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest packages/api/tests/test_engine_registry.py -k "run_" -v`
Expected: FAIL — `AttributeError: ... has no attribute 'run_stt'`.

- [ ] **Step 4: Append dispatch functions to `engine_registry.py`**

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest packages/api/tests/test_engine_registry.py -v`
Expected: all tests PASS (14 total).

- [ ] **Step 6: Commit**

```bash
git add packages/api/pyproject.toml packages/api/src/resona_api/engine_registry.py packages/api/tests/test_engine_registry.py uv.lock
git commit -m "feat(api): engine_registry STT/TTS dispatch"
```

---

## Task 9: `audio_routes` — `GET /v1/engines` discovery

**Files:**
- Create: `packages/api/src/resona_api/audio_routes.py`
- Modify: `packages/api/tests/conftest.py:62-67` (`test_app` fixture)
- Create: `packages/api/tests/test_audio_routes.py`

- [ ] **Step 1: Update the `test_app` fixture in `conftest.py`**

Replace the `test_app` fixture body so it includes the audio router:

```python
@pytest.fixture(scope="session")
def test_app():
    """Minimal FastAPI app with the job + audio routers (no lifespan)."""
    from resona_api.endpoints import router
    from resona_api.audio_routes import router as audio_router
    app = FastAPI()
    app.include_router(router)
    app.include_router(audio_router)
    return app
```

- [ ] **Step 2: Write the failing test `test_audio_routes.py`**

```python
"""Tests for the OpenAI-compatible /v1/audio/* + /v1/engines routes."""
from unittest.mock import patch

from resona_api import engine_registry as reg


def _catalogue():
    return [
        reg.EngineInfo("faster-whisper", "local", ["stt"], True, True, ["large-v3"]),
        reg.EngineInfo("deepgram", "cloud", ["stt", "tts"], False, True,
                       ["nova-3"], provider="deepgram"),
    ]


def test_list_engines_returns_catalogue(client):
    with patch.object(reg, "get_catalogue", return_value=_catalogue()):
        resp = client.get("/v1/engines")
    assert resp.status_code == 200
    body = resp.json()
    names = {e["name"] for e in body["engines"]}
    assert names == {"faster-whisper", "deepgram"}
    fw = next(e for e in body["engines"] if e["name"] == "faster-whisper")
    assert fw["private"] is True
    assert fw["kind"] == "local"
    assert "url" not in fw
    assert body["default"] == "faster-whisper"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest packages/api/tests/test_audio_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: resona_api.audio_routes`.

- [ ] **Step 4: Create `audio_routes.py`** (engines route + error helper)

```python
"""OpenAI-compatible /v1/audio/* endpoints and /v1/engines discovery."""
import logging

from fastapi import APIRouter, Depends, HTTPException

from . import engine_registry as reg
from .auth import verify_api_key

log = logging.getLogger(__name__)
router = APIRouter()


def _http_error(exc: Exception) -> HTTPException:
    """Map a registry or provider error to an HTTPException."""
    if isinstance(exc, (reg.EngineNotFoundError, reg.CapabilityError,
                        reg.PrivacyViolationError)):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, reg.EngineUnavailableError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, reg.NoEngineError):
        return HTTPException(status_code=409, detail=str(exc))
    name = type(exc).__name__
    if name == "MissingAPIKeyError":
        return HTTPException(status_code=503, detail=str(exc))
    if name == "ProviderHTTPError":
        return HTTPException(status_code=502, detail=str(exc))
    if name in ("CloudTTSError", "CloudSTTError"):
        return HTTPException(status_code=400, detail=str(exc))
    raise exc


@router.get("/v1/engines", tags=["Engines"])
def list_engines(api_key: str = Depends(verify_api_key)):
    """List every engine this gateway exposes, with capabilities and status."""
    catalogue = reg.get_catalogue(fresh=True)
    return {
        "engines": [
            {
                "name": e.name,
                "kind": e.kind,
                "capabilities": e.capabilities,
                "private": e.private,
                "available": e.available,
                "models": e.models,
            }
            for e in catalogue
        ],
        "default": reg.effective_default("stt", catalogue=catalogue),
    }
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest packages/api/tests/test_audio_routes.py -v`
Expected: `test_list_engines_returns_catalogue` PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/api/src/resona_api/audio_routes.py packages/api/tests/conftest.py packages/api/tests/test_audio_routes.py
git commit -m "feat(api): GET /v1/engines discovery route"
```

---

## Task 10: `audio_routes` — `POST /v1/audio/transcriptions`

**Files:**
- Modify: `packages/api/src/resona_api/audio_routes.py` (append)
- Modify: `packages/api/tests/test_audio_routes.py` (append)

- [ ] **Step 1: Append failing tests to `test_audio_routes.py`**

```python
def test_transcription_json_format(client, wav_bytes):
    info = _catalogue()[0]
    with patch.object(reg, "resolve", return_value=info), \
         patch.object(reg, "run_stt",
                       return_value={"text": "hallo welt", "language": "de",
                                     "segments": []}):
        resp = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("a.wav", wav_bytes, "audio/wav")},
            data={"response_format": "json"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"text": "hallo welt"}


def test_transcription_text_format(client, wav_bytes):
    info = _catalogue()[0]
    with patch.object(reg, "resolve", return_value=info), \
         patch.object(reg, "run_stt",
                       return_value={"text": "nur text", "language": "de",
                                     "segments": []}):
        resp = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("a.wav", wav_bytes, "audio/wav")},
            data={"response_format": "text"},
        )
    assert resp.status_code == 200
    assert resp.text == "nur text"


def test_transcription_private_refuses_cloud(client, wav_bytes):
    err = reg.PrivacyViolationError("engine 'deepgram' is not private")
    with patch.object(reg, "resolve", side_effect=err):
        resp = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("a.wav", wav_bytes, "audio/wav")},
            data={"engine": "deepgram", "private": "true"},
        )
    assert resp.status_code == 400
    assert "not private" in resp.json()["detail"]


def test_transcription_unknown_engine(client, wav_bytes):
    with patch.object(reg, "resolve",
                      side_effect=reg.EngineNotFoundError("unknown engine 'x'")):
        resp = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("a.wav", wav_bytes, "audio/wav")},
            data={"engine": "x"},
        )
    assert resp.status_code == 400
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/api/tests/test_audio_routes.py -k transcription -v`
Expected: FAIL — 404 (route does not exist).

- [ ] **Step 3: Append the transcriptions route to `audio_routes.py`**

Add these imports to the top of the file (merge with the existing import block):

```python
import tempfile
from pathlib import Path

from fastapi import File, Form, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

from .db.utils import get_active_replacements
from .endpoints import validate_audio_file
from resona_postprocess.replacements import apply_replacements
```

Append the route:

```python
@router.post("/v1/audio/transcriptions", tags=["Audio"])
async def create_transcription(
    file: UploadFile = File(...),
    model: str | None = Form(default=None),
    language: str = Form(default="de"),
    prompt: str = Form(default=""),
    temperature: float | None = Form(default=None),
    response_format: str = Form(default="json"),
    engine: str | None = Form(default=None),
    private: bool = Form(default=False),
    api_key: str = Depends(verify_api_key),
):
    """OpenAI-compatible synchronous speech-to-text."""
    validate_audio_file(file)
    try:
        info = reg.resolve(engine, "stt", private)
    except reg.EngineError as exc:
        raise _http_error(exc)

    suffix = Path(file.filename or "audio").suffix or ".bin"
    content = await file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        result = reg.run_stt(
            info, tmp_path, language=language, model=model, prompt=prompt
        )
    except Exception as exc:
        raise _http_error(exc)
    finally:
        tmp_path.unlink(missing_ok=True)

    text = result.get("text", "")
    replacements = get_active_replacements()
    if replacements:
        text = apply_replacements(text, replacements)

    if response_format == "text":
        return PlainTextResponse(text)
    if response_format == "verbose_json":
        segments = result.get("segments", [])
        duration = segments[-1]["end"] if segments else 0.0
        return JSONResponse({
            "text": text,
            "language": result.get("language", language),
            "duration": duration,
            "segments": segments,
        })
    return JSONResponse({"text": text})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest packages/api/tests/test_audio_routes.py -k transcription -v`
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/api/src/resona_api/audio_routes.py packages/api/tests/test_audio_routes.py
git commit -m "feat(api): POST /v1/audio/transcriptions endpoint"
```

---

## Task 11: `audio_routes` — `POST /v1/audio/speech`

**Files:**
- Modify: `packages/api/src/resona_api/audio_routes.py` (append)
- Modify: `packages/api/tests/test_audio_routes.py` (append)

- [ ] **Step 1: Append failing tests to `test_audio_routes.py`**

```python
def test_speech_returns_audio(client):
    info = _catalogue()[1]
    with patch.object(reg, "resolve", return_value=info), \
         patch.object(reg, "run_tts",
                       return_value={"audio": b"mp3bytes",
                                     "content_type": "audio/mpeg"}):
        resp = client.post(
            "/v1/audio/speech",
            json={"input": "hallo welt", "engine": "deepgram"},
        )
    assert resp.status_code == 200
    assert resp.content == b"mp3bytes"
    assert resp.headers["content-type"] == "audio/mpeg"


def test_speech_private_yields_409(client):
    err = reg.NoEngineError("no private engine available for tts")
    with patch.object(reg, "resolve", side_effect=err):
        resp = client.post(
            "/v1/audio/speech",
            json={"input": "geheim", "private": True},
        )
    assert resp.status_code == 409
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/api/tests/test_audio_routes.py -k speech -v`
Expected: FAIL — 404 (route does not exist).

- [ ] **Step 3: Append the speech route to `audio_routes.py`**

Add to the import block:

```python
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
```

Append:

```python
class SpeechRequest(BaseModel):
    """Request body for POST /v1/audio/speech."""

    model: str | None = None
    input: str
    voice: str | None = None
    response_format: str = "mp3"
    speed: float | None = None
    engine: str | None = None
    private: bool = False


@router.post("/v1/audio/speech", tags=["Audio"])
def create_speech(
    body: SpeechRequest,
    api_key: str = Depends(verify_api_key),
):
    """OpenAI-compatible synchronous text-to-speech (cloud engines only)."""
    try:
        info = reg.resolve(body.engine, "tts", body.private)
    except reg.EngineError as exc:
        raise _http_error(exc)
    try:
        result = reg.run_tts(
            info,
            body.input,
            model=body.model,
            voice=body.voice,
            response_format=body.response_format,
            speed=body.speed,
        )
    except Exception as exc:
        raise _http_error(exc)
    return StreamingResponse(
        iter([result["audio"]]), media_type=result["content_type"]
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest packages/api/tests/test_audio_routes.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/api/src/resona_api/audio_routes.py packages/api/tests/test_audio_routes.py
git commit -m "feat(api): POST /v1/audio/speech endpoint"
```

---

## Task 12: `Job.engine` column + idempotent migration

**Files:**
- Modify: `packages/api/src/resona_api/db/models.py` (Job class)
- Modify: `packages/api/src/resona_api/db/utils.py` (`register_job`)
- Modify: `packages/api/src/resona_api/db/engine.py` (`create_db_and_tables`)
- Modify: `packages/api/tests/test_db_utils.py` (append)

- [ ] **Step 1: Read `db/utils.py` and `db/engine.py`**

Run: `cat packages/api/src/resona_api/db/utils.py packages/api/src/resona_api/db/engine.py`
Note the current `register_job` signature and the `create_db_and_tables` body.

- [ ] **Step 2: Append a failing test to `test_db_utils.py`**

```python
def test_register_job_stores_engine():
    from resona_api.db.utils import register_job
    from resona_api.db.models import Job
    from resona_api.db.engine import engine
    from sqlmodel import Session, select

    job = register_job(filename="x.wav", upload_name="x.wav",
                        keep=True, translate=False, engine="deepgram")
    with Session(engine) as session:
        stored = session.exec(select(Job).where(Job.id == job.id)).first()
    assert stored.engine == "deepgram"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest packages/api/tests/test_db_utils.py::test_register_job_stores_engine -v`
Expected: FAIL — `TypeError: register_job() got an unexpected keyword argument 'engine'`.

- [ ] **Step 4: Add the `engine` column to `Job` in `db/models.py`**

In the `Job` class, after the `model` field, add:

```python
    engine: Optional[str] = Field(default=None)
```

- [ ] **Step 5: Add `engine` to `register_job` in `db/utils.py`**

Add an `engine: str | None = None` parameter to `register_job` and pass it through to the `Job(...)` constructor (i.e. add `engine=engine` to the `Job` instantiation).

- [ ] **Step 6: Add the idempotent migration to `create_db_and_tables` in `db/engine.py`**

After the existing `SQLModel.metadata.create_all(engine)` call inside `create_db_and_tables`, append:

```python
    # Idempotent migration: add Job.engine to pre-existing databases.
    from sqlalchemy import text
    with engine.connect() as conn:
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(job)"))]
        if "engine" not in cols:
            conn.execute(text("ALTER TABLE job ADD COLUMN engine VARCHAR"))
            conn.commit()
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `uv run pytest packages/api/tests/test_db_utils.py -v`
Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add packages/api/src/resona_api/db/
git add packages/api/tests/test_db_utils.py
git commit -m "feat(api): Job.engine column with idempotent migration"
```

---

## Task 13: Rewire `tasks_transcribe` and `POST /jobs` through the registry

**Files:**
- Modify: `packages/api/src/resona_api/tasks_transcribe.py`
- Modify: `packages/api/src/resona_api/app.py`
- Modify: `packages/api/src/resona_api/endpoints.py` (`submit_jobs`)
- Modify: `packages/api/tests/test_cloud_routing.py` and `test_tasks.py` as needed

- [ ] **Step 1: Inspect the affected tests**

Run: `cat packages/api/tests/test_cloud_routing.py packages/api/tests/test_tasks.py`
`test_cloud_routing.py` exercises the old `RESONA_CLOUD_ENGINE` path — those tests will be replaced; `test_tasks.py` exercises the engine-server path.

- [ ] **Step 2: Rewrite `tasks_transcribe.py`**

Replace the whole file with:

```python
import logging
import os
from threading import Thread
from datetime import datetime

from sqlmodel import Session, select

from resona_postprocess.pipeline import PostprocessPipeline
from resona_postprocess.replacements import apply_replacements

from . import engine_registry as reg
from .formatting import write_md_file
from .utils import update_job_attributes_from_result
from .db.models import Job, JobStatus
from .db.engine import engine
from .db.utils import get_active_replacements, get_active_initial_prompts_string
from .paths import FILE_PATH

log = logging.getLogger(__name__)


class TranscribeTask(Thread):
    """Background task: dequeue PENDING jobs and transcribe via the registry."""

    def __init__(self, shutdown_event):
        super().__init__(daemon=True)
        self.shutdown_event = shutdown_event

    def run(self, *args, **kwargs):
        log.info("TranscribeTask started")
        while not self.shutdown_event.is_set():
            try:
                self._process_next_job()
            except Exception as e:
                log.error(f"Unexpected error in TranscribeTask loop: {e}",
                          exc_info=True)
            self.shutdown_event.wait(timeout=1.0)
        log.info("TranscribeTask stopped")

    def _process_next_job(self):
        with Session(engine) as session:
            statement = (
                select(Job)
                .where(Job.status.in_([JobStatus.PENDING]))
                .order_by(Job.created_at.asc())
            )
            job = session.exec(statement).first()
            if job is None:
                return

            log.info(f"Starting transcription for job {job.id}")
            job.status = JobStatus.PROCESSING
            job.updated_at = datetime.utcnow()
            session.add(job)
            session.commit()

            try:
                filepath = FILE_PATH / job.filename
                if not os.path.exists(filepath):
                    raise FileNotFoundError(f"Audio file not found: {filepath}")

                initial_prompt = get_active_initial_prompts_string()
                info = reg.resolve(job.engine or None, "stt", private=False)
                asr_result = reg.run_stt(
                    info,
                    filepath,
                    language="de",
                    prompt=initial_prompt,
                    task="translate" if job.translate else "transcribe",
                )
                log.info(f"Job {job.id}: ASR completed via '{info.name}'")

                update_job_attributes_from_result(job, asr_result)

                replacements = get_active_replacements()
                pipeline = PostprocessPipeline()
                if replacements:
                    pipeline.add(
                        "replacements",
                        lambda t, r=replacements: apply_replacements(t, r),
                    )
                job.md = pipeline.run(job.transcript)

                try:
                    write_md_file(job.id, job.filename, job.md, job.keepfile)
                    log.info(f"Job {job.id}: wrote MD file")
                except Exception as e_md:
                    log.error(f"Job {job.id}: failed to write MD file: {e_md}")

                job.status = JobStatus.COMPLETED
                job.processed = True
                job.error_message = None
                job.updated_at = datetime.utcnow()
                session.add(job)
                session.commit()
                log.info(f"Job {job.id}: completed")

            except FileNotFoundError as e:
                log.error(f"Job {job.id}: file not found: {e}")
                job.status = JobStatus.FAILED
                job.error_message = f"File not found: {str(e)}"
                job.updated_at = datetime.utcnow()
                session.add(job)
                session.commit()

            except Exception as e:
                log.error(f"Job {job.id}: unexpected error: {e}", exc_info=True)
                job.status = JobStatus.FAILED
                job.error_message = f"Unexpected error: {str(e)}"
                job.updated_at = datetime.utcnow()
                session.add(job)
                session.commit()
```

- [ ] **Step 3: Update `app.py`**

In `app.py`:
- Remove the `from .engine_client import EngineClient` import.
- Remove the `_engine_client` global and its references.
- In `lifespan`, delete the `engine_url = config(...)` / `_engine_client = EngineClient(...)` lines and the `_engine_client.close()` line in shutdown.
- Change `transcribe_task = TranscribeTask(shutdown_event, _engine_client)` to `transcribe_task = TranscribeTask(shutdown_event)`.
- After `from .endpoints import router`, add `from .audio_routes import router as audio_router` and `app.include_router(audio_router)`.
- Add to `tags_metadata`: `{"name": "Audio", "description": "OpenAI-compatible speech API."}` and `{"name": "Engines", "description": "Engine discovery."}`.

- [ ] **Step 4: Update `submit_jobs` in `endpoints.py`**

Add an `engine: str | None = Form(default=None)` parameter to `submit_jobs`, and pass `engine=engine` to the `register_job(...)` call.

- [ ] **Step 5: Replace `test_cloud_routing.py`**

Replace its contents with a registry-routing test:

```python
"""The async job task routes through the engine registry."""
from unittest.mock import patch

from resona_api import engine_registry as reg
from resona_api.tasks_transcribe import TranscribeTask
from resona_api.db.utils import register_job
from resona_api.db.models import Job, JobStatus
from resona_api.db.engine import engine as db_engine
from resona_api.paths import FILE_PATH
from sqlmodel import Session, select
from threading import Event


def test_job_routes_through_registry(wav_bytes):
    (FILE_PATH / "routed.wav").write_bytes(wav_bytes)
    job = register_job(filename="routed.wav", upload_name="routed.wav",
                        keep=True, translate=False, engine="deepgram")

    info = reg.EngineInfo("deepgram", "cloud", ["stt", "tts"], False, True,
                          [], provider="deepgram")
    with patch.object(reg, "resolve", return_value=info) as resolve, \
         patch.object(reg, "run_stt",
                      return_value={"text": "ok", "language": "de",
                                    "segments": []}):
        task = TranscribeTask(Event())
        task._process_next_job()

    resolve.assert_called_once()
    assert resolve.call_args[0][0] == "deepgram"
    with Session(db_engine) as session:
        stored = session.exec(select(Job).where(Job.id == job.id)).first()
    assert stored.status == JobStatus.COMPLETED
```

- [ ] **Step 6: Run the api test suite**

Run: `uv run pytest packages/api/tests/ -v`
Expected: all tests PASS. If `test_tasks.py` patches `EngineClient.transcribe`, update it to patch `resona_api.engine_registry.run_stt` instead, returning `{"text": ..., "language": "de", "segments": []}`.

- [ ] **Step 7: Commit**

```bash
git add packages/api/src/resona_api/ packages/api/tests/
git commit -m "feat(api): route async jobs through the engine registry"
```

---

## Task 14: Multi-engine Docker Compose

**Files:**
- Modify: `docker-compose.resona.yml`

- [ ] **Step 1: Rewrite `docker-compose.resona.yml`**

```yaml
services:
  engine-faster-whisper:
    profiles: ["faster-whisper"]
    build:
      context: .
      dockerfile: packages/engine-faster-whisper/Dockerfile
    ports:
      - "7001:7001"
    volumes:
      - ${HOME}/.cache/huggingface:/root/.cache/huggingface
    environment:
      RESONA_ENGINE: faster-whisper
      DEFAULT_FASTWHISPER_MODEL: large-v3
      LOGLEVEL: info
    deploy:
      resources:
        reservations:
          devices:
            - capabilities: [gpu]
    healthcheck:
      test: ["CMD", "python3.12", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:7001/health')"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 300s
    restart: unless-stopped

  engine-whisper:
    profiles: ["whisper"]
    build:
      context: .
      dockerfile: packages/engine-whisper/Dockerfile
    ports:
      - "7002:7001"
    volumes:
      - ${HOME}/.cache/huggingface:/root/.cache/huggingface
    environment:
      RESONA_ENGINE: whisper
      DEFAULT_WHISPER_MODEL: large-v3
      LOGLEVEL: info
    deploy:
      resources:
        reservations:
          devices:
            - capabilities: [gpu]
    healthcheck:
      test: ["CMD", "python3.12", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:7001/health')"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 300s
    restart: unless-stopped

  engine-voxtral:
    profiles: ["voxtral"]
    build:
      context: .
      dockerfile: packages/engine-voxtral/Dockerfile
    ports:
      - "7003:7001"
    volumes:
      - ${HOME}/.cache/huggingface:/root/.cache/huggingface
    environment:
      RESONA_ENGINE: voxtral
      DEFAULT_VOXTRAL_MODEL: openai/whisper-large-v3
      LOGLEVEL: info
    deploy:
      resources:
        reservations:
          devices:
            - capabilities: [gpu]
    healthcheck:
      test: ["CMD", "python3.12", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:7001/health')"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 300s
    restart: unless-stopped

  api:
    build:
      context: .
      dockerfile: packages/api/Dockerfile
    ports:
      - "7000:7000"
    volumes:
      - ./data:/app/data
    environment:
      RESONA_ENGINE_URLS: http://engine-faster-whisper:7001,http://engine-whisper:7001,http://engine-voxtral:7001
      DATA_PATH: /app/data
      LOGLEVEL: info
    env_file:
      - .env
    restart: unless-stopped
```

Notes for the implementer: the `api` service no longer has a `depends_on` on a
single engine — it tolerates absent backends (each shows `available: false`).
The engine services keep their own host-side ports (`7001`/`7002`/`7003`) but
the gateway reaches them on the internal `:7001` of each container.

- [ ] **Step 2: Verify the compose file parses**

Run: `docker compose -f docker-compose.resona.yml --profile faster-whisper config -q`
Expected: no output, exit 0 (valid config). If `docker` is unavailable in the
environment, skip with a note.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.resona.yml
git commit -m "feat(docker): multi-engine compose with per-engine profiles"
```

---

## Task 15: Documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 1: Update `CLAUDE.md`**

- In "Package responsibilities", add a `resona-cloud-tts` section mirroring the
  `resona-cloud-stt` one (types `SpeechResult`, errors, registry, three
  providers `openai`/`elevenlabs`/`deepgram` with `synthesize()`).
- Under `resona-api`, add `engine_registry.py` (catalogue/resolution/dispatch)
  and `audio_routes.py` (`/v1/audio/*` + `/v1/engines`) to the file list.
- In the project-structure tree, add `cloud-tts/` next to `cloud-stt/`.
- In the env-var table: add `RESONA_ENGINE_URLS` and `RESONA_DEFAULT_ENGINE`;
  remove `RESONA_ENGINE_URL`, `RESONA_CLOUD_ENGINE`, `RESONA_CLOUD_MODEL`,
  `RESONA_CLOUD_OPTIONS`; note the cloud keys now also enable TTS.
- Update the engine-server `/health` description to mention `{engine, models}`.
- In "Job flow", note that the async path now resolves via `engine_registry`.

- [ ] **Step 2: Update `README.md`**

- Document the OpenAI-compatible API: `POST /v1/audio/transcriptions`,
  `POST /v1/audio/speech`, `GET /v1/engines`.
- Document running multiple local backends and the Compose profile usage:
  `docker compose -f docker-compose.resona.yml --profile faster-whisper --profile whisper up`.
- Document cloud STT/TTS activation via `DEEPGRAM_API_KEY` / `OPENAI_API_KEY` /
  `ELEVENLABS_API_KEY`, and the `engine` / `private` request fields.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: unified STT/TTS API, cloud-tts package, multi-engine Docker"
```

---

## Task 16: Full verification

- [ ] **Step 1: Run the entire test suite**

Run: `uv run pytest`
Expected: all tests PASS, no errors. Investigate and fix any failure before
proceeding — do not claim completion on a red suite.

- [ ] **Step 2: Smoke-check imports**

Run: `uv run python -c "import resona_cloud_tts, resona_api.engine_registry, resona_api.audio_routes; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Final commit (if anything was fixed)**

```bash
git add -A
git commit -m "test: fixes from full-suite verification"
```

---

## Self-review notes

- **Spec coverage:** `resona-cloud-tts` (Tasks 1-4) ↔ spec §5; `engine-server`
  `/health` (Task 5) ↔ §8; `engine_registry` catalogue + resolve + dispatch
  (Tasks 6-8) ↔ §6; `/v1/engines` (Task 9) ↔ §7.3; `/v1/audio/transcriptions`
  (Task 10) ↔ §7.1; `/v1/audio/speech` (Task 11) ↔ §7.2; `Job.engine` +
  migration (Task 12) and async rewire (Task 13) ↔ §9; env-var changes ↔ §10
  (Tasks 8, 13, 14); Docker (Task 14) ↔ §11; error mapping (Task 9 `_http_error`)
  ↔ §12; docs (Task 15) ↔ §14.
- **Type consistency:** `EngineInfo`, `EngineError` subclasses, `run_stt`/
  `run_tts`, `SpeechResult`, and `_http_error` names are used identically across
  Tasks 6-13.
- **Out of scope (unchanged from spec §15):** transcription logging, encrypted
  retention, corrected-transcript upload, Keycloak, local TTS, streaming.
