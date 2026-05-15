# Cloud Engines + Private Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three cloud speech-to-text engines (Deepgram, ElevenLabs, OpenAI) usable from the `resona` CLI and `resona-api`, plus a `private` classification with `--private` so sensitive audio never leaves user-controlled infrastructure.

**Architecture:** A new lean `httpx`-only workspace package `resona-cloud-stt` wraps the three providers behind one pure `transcribe(...)` function per provider that normalizes responses to `TranscriptionResult` (`{text, language, segments}`). `EngineEntry` in `resona-client` gains cloud fields; `resolve_engine()` learns name-pinning and `private_only` filtering. The CLI's `--engine` flag becomes a unified selector routing to a new `CloudEngine`, `ResonaClient`, or the local fallback. `resona-api` routes jobs to the cloud package when `RESONA_CLOUD_ENGINE` is set; postprocessing stays local.

**Tech Stack:** Python 3.12, uv workspace (src-layout), `httpx`, `typer`, `respx` (test mocking), `pytest`, `python-decouple` (`config()`), SQLModel.

**Assumption:** This plan lands **after** Plan 1 (the `backend`→`engine` rename) is merged. All names below are post-rename: `EngineConfig`, `EngineEntry`, `resolve_engine`, `default_engine`, the `resona.engines` entry-point group, `RESONA_ENGINE`, the `resona engines` subcommand, the `--engine` flag, `apps/resona-cli/src/resona_cli/engines.py`, `apps/resona-cli/tests/test_engines.py`. The `config.json` top-level keys are `engines` / `default_engine`.

---

## File Structure

### Created

| Path | Responsibility |
|------|----------------|
| `packages/cloud-stt/pyproject.toml` | Workspace member; deps: `httpx`. Wheel target `src/resona_cloud_stt`. |
| `packages/cloud-stt/src/resona_cloud_stt/__init__.py` | Package marker; re-exports `get_provider`, error classes, `TranscriptionResult`. |
| `packages/cloud-stt/src/resona_cloud_stt/types.py` | `TranscriptionResult` TypedDict (`{text, language, segments}`). |
| `packages/cloud-stt/src/resona_cloud_stt/errors.py` | `CloudSTTError`, `MissingAPIKeyError`, `ProviderHTTPError`. |
| `packages/cloud-stt/src/resona_cloud_stt/registry.py` | `PROVIDERS`, `PROVIDER_ENV_KEYS`, `DEFAULT_MODELS`, `get_provider()`. |
| `packages/cloud-stt/src/resona_cloud_stt/providers/__init__.py` | Provider sub-package marker. |
| `packages/cloud-stt/src/resona_cloud_stt/providers/deepgram.py` | Deepgram `transcribe()` — raw-body POST to `/v1/listen`. |
| `packages/cloud-stt/src/resona_cloud_stt/providers/elevenlabs.py` | ElevenLabs `transcribe()` — multipart POST to `/v1/speech-to-text`. |
| `packages/cloud-stt/src/resona_cloud_stt/providers/openai.py` | OpenAI `transcribe()` — multipart POST to `/v1/audio/transcriptions`. |
| `packages/cloud-stt/tests/conftest.py` | Shared `wav_path` fixture writing a tiny WAV under `tests/fixtures/`. |
| `packages/cloud-stt/tests/test_registry.py` | Tests for `get_provider()`, env-key lookup, default models. |
| `packages/cloud-stt/tests/test_deepgram.py` | `respx` tests for the Deepgram provider. |
| `packages/cloud-stt/tests/test_elevenlabs.py` | `respx` tests for the ElevenLabs provider. |
| `packages/cloud-stt/tests/test_openai.py` | `respx` tests for the OpenAI provider. |
| `apps/resona-cli/tests/test_cloud_engine.py` | Tests for `CloudEngine` in `resona_cli/engine.py`. |
| `packages/api/tests/test_cloud_routing.py` | Tests for `RESONA_CLOUD_ENGINE` routing in `tasks_transcribe`. |

### Modified

| Path | Change |
|------|--------|
| `pyproject.toml` (root) | `resona-cloud-stt` is picked up by `members = ["packages/*", ...]` — no edit needed; verify only. |
| `packages/client/src/resona_client/config.py` | `EngineEntry` cloud fields + `is_private()`/`is_usable()`; `EngineConfig` validation + `default_private`; `resolve_engine(name=, private_only=)` + `compose_dir` hardening. |
| `packages/client/tests/test_config.py` | New tests for cloud fields, validation, name-pinning, `private_only`, missing-`compose_dir` skip. |
| `apps/resona-cli/src/resona_cli/engine.py` | New `CloudEngine` class implementing the `Engine` protocol. |
| `apps/resona-cli/src/resona_cli/engines.py` | `engines list` merged view; `engines add` cloud options; built-in-name collision rejection. |
| `apps/resona-cli/tests/test_engines.py` | New tests for merged list, cloud `add`, `--option`, collision rejection. |
| `apps/resona-cli/src/resona_cli/transcribe.py` | Unified `--engine` resolution + routing; `--private`/`--no-private`. |
| `apps/resona-cli/tests/test_transcribe.py` | New tests for `--engine` pinning, cloud routing, `--private` refusal, `default_private`. |
| `packages/api/src/resona_api/tasks_transcribe.py` | Route to `resona-cloud-stt` when `RESONA_CLOUD_ENGINE` is set. |
| `packages/api/pyproject.toml` | Add `resona-cloud-stt` workspace dependency. |
| `apps/resona-cli/pyproject.toml` | Add `resona-cloud-stt` workspace dependency. |
| `CLAUDE.md` | Document the cloud package, engines, env vars, `--engine`/`--private`. |
| `README.md` | Same docs additions for end users. |

---

## Task 1 — `resona-cloud-stt`: package skeleton + `types.py`

**Files:**
- Create: `packages/cloud-stt/pyproject.toml`
- Create: `packages/cloud-stt/src/resona_cloud_stt/__init__.py`
- Create: `packages/cloud-stt/src/resona_cloud_stt/types.py`
- Create: `packages/cloud-stt/tests/test_registry.py` (placeholder import test for now)

Steps:

- [ ] 1. Write the failing test. Create `packages/cloud-stt/tests/test_registry.py`:
```python
"""Tests for resona_cloud_stt — package skeleton, types, registry."""
from resona_cloud_stt.types import TranscriptionResult


def test_transcription_result_shape():
    result: TranscriptionResult = {
        "text": "hello",
        "language": "en",
        "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}],
    }
    assert result["text"] == "hello"
    assert result["language"] == "en"
    assert result["segments"][0]["start"] == 0.0
```

- [ ] 2. Run it to confirm it fails. `uv run pytest packages/cloud-stt/tests/test_registry.py` — fails with `ModuleNotFoundError: No module named 'resona_cloud_stt'` (the package does not exist yet).

- [ ] 3. Create `packages/cloud-stt/pyproject.toml`:
```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "resona-cloud-stt"
version = "0.1.0"
description = "Cloud speech-to-text providers (Deepgram, ElevenLabs, OpenAI) for Resona"
requires-python = ">=3.12"
dependencies = [
    "httpx>=0.28.1",
]

[tool.hatch.build.targets.wheel]
packages = ["src/resona_cloud_stt"]
```

- [ ] 4. Create `packages/cloud-stt/src/resona_cloud_stt/types.py`:
```python
"""Normalized result type shared by every cloud provider."""
from typing import TypedDict


class TranscriptionResult(TypedDict):
    """Return type for all cloud provider transcribe() functions.

    ``segments`` is a list of ``{start: float, end: float, text: str}`` dicts.
    """
    text: str
    language: str
    segments: list[dict]
```

- [ ] 5. Create `packages/cloud-stt/src/resona_cloud_stt/__init__.py`:
```python
"""resona-cloud-stt — cloud speech-to-text provider wrappers for Resona."""
from .types import TranscriptionResult

__all__ = ["TranscriptionResult"]
```

- [ ] 6. Sync the workspace so the new member is installed: `uv sync --all-packages --no-build-isolation-package openai-whisper`.

- [ ] 7. Run the test to confirm it passes. `uv run pytest packages/cloud-stt/tests/test_registry.py` — 1 passed.

- [ ] 8. Commit:
```
git commit -am "feat(cloud-stt): scaffold resona-cloud-stt package with TranscriptionResult type"
```

---

## Task 2 — `resona-cloud-stt`: `errors.py`

**Files:**
- Create: `packages/cloud-stt/src/resona_cloud_stt/errors.py`
- Modify: `packages/cloud-stt/src/resona_cloud_stt/__init__.py`
- Modify: `packages/cloud-stt/tests/test_registry.py`

Steps:

- [ ] 1. Write the failing test. Append to `packages/cloud-stt/tests/test_registry.py`:
```python
from resona_cloud_stt.errors import (
    CloudSTTError,
    MissingAPIKeyError,
    ProviderHTTPError,
)


def test_missing_api_key_error_is_cloud_stt_error():
    err = MissingAPIKeyError("DEEPGRAM_API_KEY")
    assert isinstance(err, CloudSTTError)
    assert err.env_var == "DEEPGRAM_API_KEY"
    assert "DEEPGRAM_API_KEY" in str(err)


def test_provider_http_error_carries_status_and_body():
    err = ProviderHTTPError("deepgram", 401, "Unauthorized")
    assert isinstance(err, CloudSTTError)
    assert err.status_code == 401
    assert err.provider == "deepgram"
    assert "401" in str(err)
    assert "Unauthorized" in str(err)
```

- [ ] 2. Run it. `uv run pytest packages/cloud-stt/tests/test_registry.py -k error` — fails with `ImportError: cannot import name 'CloudSTTError'`.

- [ ] 3. Create `packages/cloud-stt/src/resona_cloud_stt/errors.py`:
```python
"""Exception hierarchy for resona-cloud-stt."""


class CloudSTTError(Exception):
    """Base class for all cloud-stt errors."""


class MissingAPIKeyError(CloudSTTError):
    """Raised when the provider's API key env var is not set.

    Attributes:
        env_var: Name of the missing environment variable.
    """

    def __init__(self, env_var: str) -> None:
        self.env_var = env_var
        super().__init__(
            f"Missing API key — set the {env_var} environment variable."
        )


class ProviderHTTPError(CloudSTTError):
    """Raised when a provider returns a non-2xx HTTP response.

    Attributes:
        provider: Provider name (``deepgram``/``elevenlabs``/``openai``).
        status_code: HTTP status code returned.
        body: Response body text (provider error message).
    """

    def __init__(self, provider: str, status_code: int, body: str) -> None:
        self.provider = provider
        self.status_code = status_code
        self.body = body
        super().__init__(
            f"{provider} returned HTTP {status_code}: {body}"
        )
```

- [ ] 4. Update `packages/cloud-stt/src/resona_cloud_stt/__init__.py`:
```python
"""resona-cloud-stt — cloud speech-to-text provider wrappers for Resona."""
from .errors import CloudSTTError, MissingAPIKeyError, ProviderHTTPError
from .types import TranscriptionResult

__all__ = [
    "TranscriptionResult",
    "CloudSTTError",
    "MissingAPIKeyError",
    "ProviderHTTPError",
]
```

- [ ] 5. Run the test. `uv run pytest packages/cloud-stt/tests/test_registry.py` — all passed.

- [ ] 6. Commit:
```
git commit -am "feat(cloud-stt): add CloudSTTError / MissingAPIKeyError / ProviderHTTPError"
```

---

## Task 3 — `resona-cloud-stt`: `registry.py`

**Files:**
- Create: `packages/cloud-stt/src/resona_cloud_stt/registry.py`
- Create: `packages/cloud-stt/src/resona_cloud_stt/providers/__init__.py`
- Modify: `packages/cloud-stt/src/resona_cloud_stt/__init__.py`
- Modify: `packages/cloud-stt/tests/test_registry.py`

Steps:

- [ ] 1. Write the failing test. Append to `packages/cloud-stt/tests/test_registry.py`:
```python
import pytest

from resona_cloud_stt.registry import (
    DEFAULT_MODELS,
    PROVIDER_ENV_KEYS,
    PROVIDERS,
    get_provider,
)


def test_providers_set_has_three_known_providers():
    assert PROVIDERS == {"deepgram", "elevenlabs", "openai"}


def test_provider_env_keys():
    assert PROVIDER_ENV_KEYS["deepgram"] == "DEEPGRAM_API_KEY"
    assert PROVIDER_ENV_KEYS["elevenlabs"] == "ELEVENLABS_API_KEY"
    assert PROVIDER_ENV_KEYS["openai"] == "OPENAI_API_KEY"


def test_default_models():
    assert DEFAULT_MODELS["deepgram"] == "nova-3"
    assert DEFAULT_MODELS["elevenlabs"] == "scribe_v1"
    assert DEFAULT_MODELS["openai"] == "whisper-1"


def test_get_provider_returns_module_with_transcribe():
    mod = get_provider("deepgram")
    assert hasattr(mod, "transcribe")


def test_get_provider_unknown_raises():
    with pytest.raises(ValueError, match="Unknown provider"):
        get_provider("nonsense")
```

- [ ] 2. Run it. `uv run pytest packages/cloud-stt/tests/test_registry.py -k registry or providers` — fails: `ModuleNotFoundError: No module named 'resona_cloud_stt.registry'`.

- [ ] 3. Create `packages/cloud-stt/src/resona_cloud_stt/providers/__init__.py`:
```python
"""Cloud STT provider modules — each exposes a uniform transcribe() function."""
```

- [ ] 4. Create `packages/cloud-stt/src/resona_cloud_stt/registry.py`:
```python
"""Provider registry — names, env keys, default models, dynamic lookup."""
import importlib
from types import ModuleType

PROVIDERS: set[str] = {"deepgram", "elevenlabs", "openai"}

PROVIDER_ENV_KEYS: dict[str, str] = {
    "deepgram": "DEEPGRAM_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "openai": "OPENAI_API_KEY",
}

DEFAULT_MODELS: dict[str, str] = {
    "deepgram": "nova-3",
    "elevenlabs": "scribe_v1",
    "openai": "whisper-1",
}


def get_provider(name: str) -> ModuleType:
    """Return the provider module for ``name`` (has a ``transcribe`` function).

    Raises:
        ValueError: if ``name`` is not a known provider.
    """
    if name not in PROVIDERS:
        raise ValueError(
            f"Unknown provider '{name}'. Known: {sorted(PROVIDERS)}"
        )
    return importlib.import_module(f"resona_cloud_stt.providers.{name}")
```

Note: `get_provider` imports lazily so this task does not require the provider modules to exist yet — the test only calls `get_provider("deepgram")`, so the deepgram module must exist. To keep the suite green, the deepgram module is built in Task 4; until then, mark `test_get_provider_returns_module_with_transcribe` with `@pytest.mark.xfail(reason="deepgram module added in Task 4", strict=True)`.

- [ ] 5. Update `packages/cloud-stt/src/resona_cloud_stt/__init__.py` to also export registry symbols:
```python
"""resona-cloud-stt — cloud speech-to-text provider wrappers for Resona."""
from .errors import CloudSTTError, MissingAPIKeyError, ProviderHTTPError
from .registry import DEFAULT_MODELS, PROVIDER_ENV_KEYS, PROVIDERS, get_provider
from .types import TranscriptionResult

__all__ = [
    "TranscriptionResult",
    "CloudSTTError",
    "MissingAPIKeyError",
    "ProviderHTTPError",
    "PROVIDERS",
    "PROVIDER_ENV_KEYS",
    "DEFAULT_MODELS",
    "get_provider",
]
```

- [ ] 6. Run the test. `uv run pytest packages/cloud-stt/tests/test_registry.py` — all passed (one `xfail`).

- [ ] 7. Commit:
```
git commit -am "feat(cloud-stt): add provider registry (names, env keys, default models, get_provider)"
```

---

## Task 4 — `resona-cloud-stt`: Deepgram provider

**Files:**
- Create: `packages/cloud-stt/src/resona_cloud_stt/providers/deepgram.py`
- Create: `packages/cloud-stt/tests/conftest.py`
- Create: `packages/cloud-stt/tests/test_deepgram.py`
- Modify: `packages/cloud-stt/tests/test_registry.py` (remove the `xfail` marker)

Steps:

- [ ] 1. Create the WAV fixture helper. Create `packages/cloud-stt/tests/conftest.py`:
```python
"""Shared fixtures for resona-cloud-stt tests."""
import io
import struct
import wave
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def wav_path() -> Path:
    """A tiny valid WAV file (160 frames silence, 16kHz mono) under tests/fixtures/."""
    FIXTURES.mkdir(exist_ok=True)
    path = FIXTURES / "silence.wav"
    if not path.exists():
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(struct.pack("<" + "h" * 160, *([0] * 160)))
        path.write_bytes(buf.getvalue())
    return path
```

- [ ] 2. Write the failing test. Create `packages/cloud-stt/tests/test_deepgram.py`:
```python
"""Tests for the Deepgram cloud provider."""
import httpx
import pytest
import respx

from resona_cloud_stt.errors import ProviderHTTPError
from resona_cloud_stt.providers import deepgram

URL = "https://api.deepgram.com/v1/listen"

_OK_BODY = {
    "results": {
        "channels": [
            {
                "alternatives": [
                    {
                        "transcript": "guten morgen",
                        "words": [
                            {"word": "guten", "start": 0.1, "end": 0.5},
                            {"word": "morgen", "start": 0.6, "end": 1.2},
                        ],
                    }
                ]
            }
        ]
    }
}


@respx.mock
def test_transcribe_parses_transcript_and_segment(wav_path):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    result = deepgram.transcribe(wav_path, api_key="dgkey", language="de")
    assert route.called
    assert result["text"] == "guten morgen"
    assert result["language"] == "de"
    assert result["segments"] == [{"start": 0.1, "end": 1.2, "text": "guten morgen"}]


@respx.mock
def test_transcribe_sends_token_auth_header_and_query_params(wav_path):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    deepgram.transcribe(wav_path, api_key="dgkey", model="nova-2", language="en")
    req = route.calls.last.request
    assert req.headers["authorization"] == "Token dgkey"
    assert req.url.params["model"] == "nova-2"
    assert req.url.params["language"] == "en"


@respx.mock
def test_transcribe_omits_language_when_none(wav_path):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    result = deepgram.transcribe(wav_path, api_key="dgkey")
    assert "language" not in route.calls.last.request.url.params
    assert result["language"] == ""


@respx.mock
def test_transcribe_maps_whitelisted_options_drops_unknown(wav_path, caplog):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    deepgram.transcribe(
        wav_path,
        api_key="dgkey",
        options={"smart_format": True, "bogus": "x"},
    )
    params = route.calls.last.request.url.params
    assert params["smart_format"] == "true"
    assert "bogus" not in params
    assert "bogus" in caplog.text


@respx.mock
def test_transcribe_empty_words_yields_zero_bounds(wav_path):
    body = {
        "results": {
            "channels": [{"alternatives": [{"transcript": "hi", "words": []}]}]
        }
    }
    respx.post(URL).mock(return_value=httpx.Response(200, json=body))
    result = deepgram.transcribe(wav_path, api_key="dgkey")
    assert result["segments"] == [{"start": 0.0, "end": 0.0, "text": "hi"}]


@respx.mock
def test_transcribe_raises_provider_http_error_on_401(wav_path):
    respx.post(URL).mock(return_value=httpx.Response(401, text="Unauthorized"))
    with pytest.raises(ProviderHTTPError) as exc:
        deepgram.transcribe(wav_path, api_key="bad")
    assert exc.value.status_code == 401
    assert exc.value.provider == "deepgram"
```

- [ ] 3. Run it. `uv run pytest packages/cloud-stt/tests/test_deepgram.py` — fails: `ModuleNotFoundError: No module named 'resona_cloud_stt.providers.deepgram'`.

- [ ] 4. Create `packages/cloud-stt/src/resona_cloud_stt/providers/deepgram.py`:
```python
"""Deepgram provider — POST raw audio bytes to /v1/listen."""
import logging
import mimetypes
from pathlib import Path

import httpx

from ..errors import ProviderHTTPError
from ..registry import DEFAULT_MODELS
from ..types import TranscriptionResult

log = logging.getLogger(__name__)

_URL = "https://api.deepgram.com/v1/listen"
_TIMEOUT = 600.0
_OPTION_KEYS = {"smart_format", "diarize", "punctuate", "numerals"}


def _filter_options(options: dict | None) -> dict:
    """Keep only whitelisted keys; warn and drop the rest."""
    if not options:
        return {}
    kept: dict = {}
    for key, value in options.items():
        if key in _OPTION_KEYS:
            kept[key] = value
        else:
            log.warning("deepgram: dropping unknown option '%s'", key)
    return kept


def transcribe(
    audio_path: Path,
    *,
    api_key: str,
    model: str | None = None,
    language: str | None = None,
    options: dict | None = None,
) -> TranscriptionResult:
    """Transcribe ``audio_path`` via the Deepgram REST API."""
    audio_path = Path(audio_path)
    params: dict = {"model": model or DEFAULT_MODELS["deepgram"]}
    if language:
        params["language"] = language
    params.update(_filter_options(options))

    content_type = mimetypes.guess_type(str(audio_path))[0] or "application/octet-stream"
    headers = {"Authorization": f"Token {api_key}", "Content-Type": content_type}

    resp = httpx.post(
        _URL,
        params=params,
        headers=headers,
        content=audio_path.read_bytes(),
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 300:
        raise ProviderHTTPError("deepgram", resp.status_code, resp.text)

    data = resp.json()
    alt = data["results"]["channels"][0]["alternatives"][0]
    text = alt.get("transcript", "")
    words = alt.get("words") or []
    start = float(words[0]["start"]) if words else 0.0
    end = float(words[-1]["end"]) if words else 0.0
    return TranscriptionResult(
        text=text,
        language=language or "",
        segments=[{"start": start, "end": end, "text": text}],
    )
```

- [ ] 5. Remove the `xfail` marker from `test_get_provider_returns_module_with_transcribe` in `test_registry.py` (the deepgram module now exists).

- [ ] 6. Run the tests. `uv run pytest packages/cloud-stt/tests/test_deepgram.py packages/cloud-stt/tests/test_registry.py` — all passed.

- [ ] 7. Commit:
```
git commit -am "feat(cloud-stt): add Deepgram provider with respx tests"
```

---

## Task 5 — `resona-cloud-stt`: ElevenLabs provider

**Files:**
- Create: `packages/cloud-stt/src/resona_cloud_stt/providers/elevenlabs.py`
- Create: `packages/cloud-stt/tests/test_elevenlabs.py`

Steps:

- [ ] 1. Write the failing test. Create `packages/cloud-stt/tests/test_elevenlabs.py`:
```python
"""Tests for the ElevenLabs cloud provider."""
import httpx
import pytest
import respx

from resona_cloud_stt.errors import ProviderHTTPError
from resona_cloud_stt.providers import elevenlabs

URL = "https://api.elevenlabs.io/v1/speech-to-text"

_OK_BODY = {
    "text": "bonjour le monde",
    "language_code": "fr",
    "words": [
        {"text": "bonjour", "start": 0.2, "end": 0.7},
        {"text": "monde", "start": 0.8, "end": 1.5},
    ],
}


@respx.mock
def test_transcribe_parses_text_language_segment(wav_path):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    result = elevenlabs.transcribe(wav_path, api_key="elkey", language="fr")
    assert route.called
    assert result["text"] == "bonjour le monde"
    assert result["language"] == "fr"
    assert result["segments"] == [{"start": 0.2, "end": 1.5, "text": "bonjour le monde"}]


@respx.mock
def test_transcribe_sends_xi_api_key_and_multipart_fields(wav_path):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    elevenlabs.transcribe(wav_path, api_key="elkey", model="scribe_v2", language="de")
    req = route.calls.last.request
    assert req.headers["xi-api-key"] == "elkey"
    body = req.content.decode("utf-8", errors="ignore")
    assert "scribe_v2" in body
    assert 'name="model_id"' in body
    assert 'name="language_code"' in body
    assert "de" in body
    assert 'name="file"' in body


@respx.mock
def test_transcribe_defaults_model_id_to_scribe_v1(wav_path):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    elevenlabs.transcribe(wav_path, api_key="elkey")
    assert "scribe_v1" in route.calls.last.request.content.decode("utf-8", errors="ignore")


@respx.mock
def test_transcribe_maps_whitelisted_options_drops_unknown(wav_path, caplog):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    elevenlabs.transcribe(
        wav_path,
        api_key="elkey",
        options={"diarize": True, "bogus": "x"},
    )
    body = route.calls.last.request.content.decode("utf-8", errors="ignore")
    assert 'name="diarize"' in body
    assert "bogus" not in body
    assert "bogus" in caplog.text


@respx.mock
def test_transcribe_raises_provider_http_error_on_400(wav_path):
    respx.post(URL).mock(return_value=httpx.Response(400, text="bad request"))
    with pytest.raises(ProviderHTTPError) as exc:
        elevenlabs.transcribe(wav_path, api_key="elkey")
    assert exc.value.status_code == 400
    assert exc.value.provider == "elevenlabs"
```

- [ ] 2. Run it. `uv run pytest packages/cloud-stt/tests/test_elevenlabs.py` — fails: `ModuleNotFoundError: No module named 'resona_cloud_stt.providers.elevenlabs'`.

- [ ] 3. Create `packages/cloud-stt/src/resona_cloud_stt/providers/elevenlabs.py`:
```python
"""ElevenLabs provider — multipart POST to /v1/speech-to-text."""
import logging
import mimetypes
from pathlib import Path

import httpx

from ..errors import ProviderHTTPError
from ..registry import DEFAULT_MODELS
from ..types import TranscriptionResult

log = logging.getLogger(__name__)

_URL = "https://api.elevenlabs.io/v1/speech-to-text"
_TIMEOUT = 600.0
_OPTION_KEYS = {"diarize", "num_speakers", "tag_audio_events"}


def _filter_options(options: dict | None) -> dict:
    """Keep only whitelisted keys; warn and drop the rest."""
    if not options:
        return {}
    kept: dict = {}
    for key, value in options.items():
        if key in _OPTION_KEYS:
            kept[key] = value
        else:
            log.warning("elevenlabs: dropping unknown option '%s'", key)
    return kept


def transcribe(
    audio_path: Path,
    *,
    api_key: str,
    model: str | None = None,
    language: str | None = None,
    options: dict | None = None,
) -> TranscriptionResult:
    """Transcribe ``audio_path`` via the ElevenLabs REST API."""
    audio_path = Path(audio_path)
    data: dict = {"model_id": model or DEFAULT_MODELS["elevenlabs"]}
    if language:
        data["language_code"] = language
    for key, value in _filter_options(options).items():
        data[key] = str(value)

    content_type = mimetypes.guess_type(str(audio_path))[0] or "application/octet-stream"
    with open(audio_path, "rb") as fh:
        resp = httpx.post(
            _URL,
            headers={"xi-api-key": api_key},
            data=data,
            files={"file": (audio_path.name, fh, content_type)},
            timeout=_TIMEOUT,
        )
    if resp.status_code >= 300:
        raise ProviderHTTPError("elevenlabs", resp.status_code, resp.text)

    body = resp.json()
    text = body.get("text", "")
    words = body.get("words") or []
    start = float(words[0]["start"]) if words else 0.0
    end = float(words[-1]["end"]) if words else 0.0
    return TranscriptionResult(
        text=text,
        language=body.get("language_code", ""),
        segments=[{"start": start, "end": end, "text": text}],
    )
```

- [ ] 4. Run the test. `uv run pytest packages/cloud-stt/tests/test_elevenlabs.py` — all passed.

- [ ] 5. Commit:
```
git commit -am "feat(cloud-stt): add ElevenLabs provider with respx tests"
```

---

## Task 6 — `resona-cloud-stt`: OpenAI provider

**Files:**
- Create: `packages/cloud-stt/src/resona_cloud_stt/providers/openai.py`
- Create: `packages/cloud-stt/tests/test_openai.py`

Steps:

- [ ] 1. Write the failing test. Create `packages/cloud-stt/tests/test_openai.py`:
```python
"""Tests for the OpenAI cloud provider."""
import httpx
import pytest
import respx

from resona_cloud_stt.errors import ProviderHTTPError
from resona_cloud_stt.providers import openai

URL = "https://api.openai.com/v1/audio/transcriptions"

_OK_BODY = {
    "text": "hello there",
    "language": "english",
    "segments": [
        {"start": 0.0, "end": 0.6, "text": "hello"},
        {"start": 0.6, "end": 1.1, "text": "there"},
    ],
}


@respx.mock
def test_transcribe_maps_verbose_json_segments_directly(wav_path):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    result = openai.transcribe(wav_path, api_key="oakey", language="en")
    assert route.called
    assert result["text"] == "hello there"
    assert result["language"] == "english"
    assert result["segments"] == [
        {"start": 0.0, "end": 0.6, "text": "hello"},
        {"start": 0.6, "end": 1.1, "text": "there"},
    ]


@respx.mock
def test_transcribe_sends_bearer_auth_and_multipart_fields(wav_path):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    openai.transcribe(wav_path, api_key="oakey", model="whisper-1", language="de")
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer oakey"
    body = req.content.decode("utf-8", errors="ignore")
    assert 'name="model"' in body
    assert "whisper-1" in body
    assert 'name="response_format"' in body
    assert "verbose_json" in body
    assert 'name="language"' in body
    assert 'name="file"' in body


@respx.mock
def test_transcribe_maps_whitelisted_options_drops_unknown(wav_path, caplog):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    openai.transcribe(
        wav_path,
        api_key="oakey",
        options={"temperature": 0.2, "bogus": "x"},
    )
    body = route.calls.last.request.content.decode("utf-8", errors="ignore")
    assert 'name="temperature"' in body
    assert "bogus" not in body
    assert "bogus" in caplog.text


@respx.mock
def test_transcribe_raises_provider_http_error_on_401(wav_path):
    respx.post(URL).mock(return_value=httpx.Response(401, text="invalid key"))
    with pytest.raises(ProviderHTTPError) as exc:
        openai.transcribe(wav_path, api_key="bad")
    assert exc.value.status_code == 401
    assert exc.value.provider == "openai"
```

- [ ] 2. Run it. `uv run pytest packages/cloud-stt/tests/test_openai.py` — fails: `ModuleNotFoundError: No module named 'resona_cloud_stt.providers.openai'`.

- [ ] 3. Create `packages/cloud-stt/src/resona_cloud_stt/providers/openai.py`:
```python
"""OpenAI provider — multipart POST to /v1/audio/transcriptions."""
import logging
import mimetypes
from pathlib import Path

import httpx

from ..errors import ProviderHTTPError
from ..registry import DEFAULT_MODELS
from ..types import TranscriptionResult

log = logging.getLogger(__name__)

_URL = "https://api.openai.com/v1/audio/transcriptions"
_TIMEOUT = 600.0
_OPTION_KEYS = {"prompt", "temperature"}


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


def transcribe(
    audio_path: Path,
    *,
    api_key: str,
    model: str | None = None,
    language: str | None = None,
    options: dict | None = None,
) -> TranscriptionResult:
    """Transcribe ``audio_path`` via the OpenAI audio transcriptions API."""
    audio_path = Path(audio_path)
    data: dict = {
        "model": model or DEFAULT_MODELS["openai"],
        "response_format": "verbose_json",
    }
    if language:
        data["language"] = language
    for key, value in _filter_options(options).items():
        data[key] = str(value)

    content_type = mimetypes.guess_type(str(audio_path))[0] or "application/octet-stream"
    with open(audio_path, "rb") as fh:
        resp = httpx.post(
            _URL,
            headers={"Authorization": f"Bearer {api_key}"},
            data=data,
            files={"file": (audio_path.name, fh, content_type)},
            timeout=_TIMEOUT,
        )
    if resp.status_code >= 300:
        raise ProviderHTTPError("openai", resp.status_code, resp.text)

    body = resp.json()
    segments = [
        {"start": float(s.get("start", 0.0)),
         "end": float(s.get("end", 0.0)),
         "text": s.get("text", "")}
        for s in body.get("segments") or []
    ]
    return TranscriptionResult(
        text=body.get("text", ""),
        language=body.get("language", ""),
        segments=segments,
    )
```

- [ ] 4. Run the test. `uv run pytest packages/cloud-stt/tests/test_openai.py` — all passed.

- [ ] 5. Run the whole cloud-stt suite. `uv run pytest packages/cloud-stt/` — all passed.

- [ ] 6. Commit:
```
git commit -am "feat(cloud-stt): add OpenAI provider with respx tests"
```

---

## Task 7 — Wire `resona-cloud-stt` into the workspace

**Files:**
- Modify: `apps/resona-cli/pyproject.toml`
- Modify: `packages/api/pyproject.toml`
- Modify: `pyproject.toml` (root — verify only, `packages/*` already globs it)

Steps:

- [ ] 1. Add the dependency to `apps/resona-cli/pyproject.toml`. In `[project] dependencies` add `"resona-cloud-stt",`, and in `[tool.uv.sources]` add `resona-cloud-stt = { workspace = true }`.

- [ ] 2. Add the dependency to `packages/api/pyproject.toml`. In `[project] dependencies` add `"resona-cloud-stt",`, and in `[tool.uv.sources]` add `resona-cloud-stt = { workspace = true }`.

- [ ] 3. Re-sync. `uv sync --all-packages --no-build-isolation-package openai-whisper`.

- [ ] 4. Verify import resolution from each consumer:
```
uv run python -c "import resona_cloud_stt; from resona_cloud_stt import get_provider; print('ok')"
```
Expected output: `ok`.

- [ ] 5. Run the full suite to confirm nothing regressed. `uv run pytest` — all passed (existing count + the new cloud-stt tests).

- [ ] 6. Commit:
```
git commit -am "build: add resona-cloud-stt as a workspace dependency of resona-cli and resona-api"
```

---

## Task 8 — `EngineEntry` cloud fields + `is_private()` / `is_usable()`

**Files:**
- Modify: `packages/client/src/resona_client/config.py`
- Modify: `packages/client/tests/test_config.py`

Steps:

- [ ] 1. Write the failing test. Append to `packages/client/tests/test_config.py`:
```python
# ── EngineEntry cloud fields ──────────────────────────────────────────────────

def test_engine_entry_defaults_are_resona_api_non_private():
    e = EngineEntry(name="srv", api_url="http://srv:7000")
    assert e.type == "resona-api"
    assert e.provider is None
    assert e.model is None
    assert e.private is False
    assert e.options == {}


def test_engine_entry_cloud_fields():
    e = EngineEntry(
        name="dg", type="cloud", provider="deepgram", model="nova-3",
        options={"smart_format": True},
    )
    assert e.api_url == ""
    assert e.provider == "deepgram"
    assert e.options == {"smart_format": True}


def test_is_private_true_for_marked_resona_api():
    assert EngineEntry(name="s", api_url="http://s:7000", private=True).is_private() is True


def test_is_private_false_for_unmarked_resona_api():
    assert EngineEntry(name="s", api_url="http://s:7000").is_private() is False


def test_is_private_always_false_for_cloud_even_if_private_flag_set():
    e = EngineEntry(name="dg", type="cloud", provider="deepgram", private=True)
    assert e.is_private() is False


def test_is_usable_cloud_true_when_env_key_set(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")
    e = EngineEntry(name="dg", type="cloud", provider="deepgram")
    assert e.is_usable() is True


def test_is_usable_cloud_false_when_env_key_missing(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    e = EngineEntry(name="dg", type="cloud", provider="deepgram")
    assert e.is_usable() is False


def test_is_usable_resona_api_probes_health():
    e = EngineEntry(name="s", api_url="http://s:7000")
    with respx.mock:
        respx.get("http://s:7000/health").mock(return_value=httpx.Response(200))
        assert e.is_usable() is True
```

- [ ] 2. Run it. `uv run pytest packages/client/tests/test_config.py -k "engine_entry or is_private or is_usable"` — fails: `EngineEntry.__init__()` rejects `type` / `provider` / etc. (`TypeError: unexpected keyword argument`).

- [ ] 3. Add the fields and methods. In `packages/client/src/resona_client/config.py`, replace the `EngineEntry` dataclass body (the renamed `BackendEntry`) — make `api_url` default to `""` and add the five cloud fields plus methods:
```python
@dataclass
class EngineEntry:
    """A single configured Resona engine (a resona-api server or a cloud provider).

    ``resona-api`` entries connect to a Resona API server; ``cloud`` entries
    name a third-party STT provider. Cloud entries have no ``api_url`` and
    never store an API key — the key is read from an environment variable.
    """

    name: str
    api_url: str = ""
    api_key: str = ""
    compose_dir: Optional[str] = None
    ssh_host: Optional[str] = None
    ssh_remote_port: Optional[int] = None
    type: str = "resona-api"               # "resona-api" | "cloud"
    provider: Optional[str] = None         # cloud: "deepgram"|"elevenlabs"|"openai"
    model: Optional[str] = None            # provider model override
    private: bool = False                  # resona-api: user-asserted privacy
    options: dict = field(default_factory=dict)

    def health_url(self) -> str:
        return self.api_url.rstrip("/") + "/health"

    def is_private(self) -> bool:
        """True if audio sent here stays user-controlled.

        Cloud entries are never private (the ``private`` flag is ignored).
        """
        if self.type == "cloud":
            return False
        return self.private

    def is_usable(self) -> bool:
        """True if this engine can currently be used.

        ``resona-api``: the ``/health`` endpoint responds 200.
        ``cloud``: the provider's API-key env var is set.
        """
        if self.type == "cloud":
            from resona_cloud_stt.registry import PROVIDER_ENV_KEYS
            env_var = PROVIDER_ENV_KEYS.get(self.provider or "")
            return bool(env_var and os.getenv(env_var))
        return is_reachable(self)
```
Add `import os` near the top of the file if not already present.

- [ ] 4. Run the test. `uv run pytest packages/client/tests/test_config.py -k "engine_entry or is_private or is_usable"` — all passed.

- [ ] 5. Run the full config suite to confirm no regression. `uv run pytest packages/client/tests/test_config.py` — all passed.

- [ ] 6. Commit:
```
git commit -am "feat(client): add cloud fields and is_private/is_usable to EngineEntry"
```

---

## Task 9 — `EngineConfig` validation + `default_private`

**Files:**
- Modify: `packages/client/src/resona_client/config.py`
- Modify: `packages/client/tests/test_config.py`

Steps:

- [ ] 1. Write the failing test. Append to `packages/client/tests/test_config.py`:
```python
# ── EngineConfig cloud validation + default_private ───────────────────────────

def test_engine_config_default_private_defaults_false():
    assert EngineConfig().default_private is False


def test_load_default_private_from_config(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"engines": [], "default_private": True}))
    monkeypatch.setattr("resona_client.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("resona_client.config._LEGACY_CONFIG_FILE", tmp_path / "nope.json")
    assert EngineConfig.load().default_private is True


def test_save_persists_default_private(tmp_path, monkeypatch):
    monkeypatch.setattr("resona_client.config.CONFIG_DIR", tmp_path)
    config_file = tmp_path / "config.json"
    monkeypatch.setattr("resona_client.config.CONFIG_FILE", config_file)
    EngineConfig(default_private=True).save()
    assert json.loads(config_file.read_text())["default_private"] is True


def test_add_rejects_cloud_entry_with_unknown_provider(tmp_path, monkeypatch):
    monkeypatch.setattr("resona_client.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("resona_client.config.CONFIG_FILE", tmp_path / "config.json")
    cfg = EngineConfig()
    with pytest.raises(ValueError, match="provider"):
        cfg.add(EngineEntry(name="bad", type="cloud", provider="nonsense"))


def test_add_accepts_valid_cloud_entry(tmp_path, monkeypatch):
    monkeypatch.setattr("resona_client.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("resona_client.config.CONFIG_FILE", tmp_path / "config.json")
    cfg = EngineConfig()
    cfg.add(EngineEntry(name="dg", type="cloud", provider="deepgram"))
    assert cfg.get("dg") is not None


def test_load_skips_invalid_cloud_entry_with_warning(tmp_path, monkeypatch, caplog):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"engines": [
        {"name": "good", "type": "cloud", "provider": "openai"},
        {"name": "bad", "type": "cloud", "provider": "nonsense"},
    ]}))
    monkeypatch.setattr("resona_client.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("resona_client.config._LEGACY_CONFIG_FILE", tmp_path / "nope.json")
    cfg = EngineConfig.load()
    names = [e.name for e in cfg.engines]
    assert names == ["good"]
    assert "bad" in caplog.text


def test_cloud_entry_round_trips_through_save_load(tmp_path, monkeypatch):
    monkeypatch.setattr("resona_client.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("resona_client.config.CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr("resona_client.config._LEGACY_CONFIG_FILE", tmp_path / "nope.json")
    EngineConfig(engines=[
        EngineEntry(name="dg", type="cloud", provider="deepgram",
                    model="nova-3", options={"smart_format": True}),
    ]).save()
    loaded = EngineConfig.load()
    assert loaded.engines[0].type == "cloud"
    assert loaded.engines[0].options == {"smart_format": True}
```

- [ ] 2. Run it. `uv run pytest packages/client/tests/test_config.py -k "default_private or cloud_entry or unknown_provider or invalid_cloud"` — fails: `EngineConfig` has no `default_private` and `add()` does not validate cloud entries.

- [ ] 3. Add a validation helper and wire it in. In `packages/client/src/resona_client/config.py`, add near the top after the imports:
```python
def _validate_cloud_entry(entry: "EngineEntry") -> None:
    """Raise ValueError if a cloud entry names an unknown provider."""
    if entry.type != "cloud":
        return
    from resona_cloud_stt.registry import PROVIDERS
    if entry.provider not in PROVIDERS:
        raise ValueError(
            f"Engine '{entry.name}': cloud entries need a provider in "
            f"{sorted(PROVIDERS)}, got {entry.provider!r}"
        )
```

- [ ] 4. Add `default_private` to the `EngineConfig` dataclass: `default_private: bool = False` (alongside `engines` and `default_engine`).

- [ ] 5. In `EngineConfig.load()`, after building `engines`, filter out invalid cloud entries with a warning, and read `default_private`:
```python
        raw_engines = data.get("engines", data.get("backends", []))
        engines: list[EngineEntry] = []
        for raw in raw_engines:
            entry = EngineEntry(**raw)
            try:
                _validate_cloud_entry(entry)
            except ValueError as e:
                log.warning(f"Skipping invalid engine entry: {e}")
                continue
            engines.append(entry)
        default_engine = data.get("default_engine", "faster-whisper")
        default_private = bool(data.get("default_private", False))
        return cls(engines=engines, default_engine=default_engine,
                   default_private=default_private)
```
(The `data.get("engines", data.get("backends", []))` legacy fallback comes from Plan 1; keep it.)

- [ ] 6. In `EngineConfig.save()`, add `"default_private": self.default_private` to the serialized `data` dict.

- [ ] 7. In `EngineConfig.add()`, call `_validate_cloud_entry(entry)` before the duplicate check:
```python
    def add(self, entry: EngineEntry) -> None:
        _validate_cloud_entry(entry)
        if self.get(entry.name):
            raise ValueError(f"Engine '{entry.name}' already exists")
        self.engines.append(entry)
        self.save()
```

- [ ] 8. Run the test. `uv run pytest packages/client/tests/test_config.py` — all passed.

- [ ] 9. Commit:
```
git commit -am "feat(client): validate cloud engine entries and add default_private to EngineConfig"
```

---

## Task 10 — `resolve_engine()`: `name=`, `private_only=`, cloud usability, `compose_dir` hardening

**Files:**
- Modify: `packages/client/src/resona_client/config.py`
- Modify: `packages/client/tests/test_config.py`

Steps:

- [ ] 1. Write the failing test. Append to `packages/client/tests/test_config.py`:
```python
# ── resolve_engine: name pinning, private_only, cloud, compose_dir ────────────

def test_resolve_engine_name_pins_specific_entry(monkeypatch):
    e1 = EngineEntry(name="a", api_url="http://a:7000")
    e2 = EngineEntry(name="b", api_url="http://b:7000")
    monkeypatch.setattr("resona_client.config.EngineConfig.load",
                        lambda: EngineConfig(engines=[e1, e2]))
    with patch("resona_client.config.is_reachable", return_value=True):
        result = resolve_engine(name="b", auto_start=False)
    assert result is e2


def test_resolve_engine_name_unknown_returns_none(monkeypatch):
    monkeypatch.setattr("resona_client.config.EngineConfig.load",
                        lambda: EngineConfig(engines=[]))
    assert resolve_engine(name="ghost", auto_start=False) is None


def test_resolve_engine_private_only_skips_non_private(monkeypatch):
    public = EngineEntry(name="pub", api_url="http://pub:7000", private=False)
    priv = EngineEntry(name="priv", api_url="http://priv:7000", private=True)
    monkeypatch.setattr("resona_client.config.EngineConfig.load",
                        lambda: EngineConfig(engines=[public, priv]))
    with patch("resona_client.config.is_reachable", return_value=True):
        result = resolve_engine(private_only=True, auto_start=False)
    assert result is priv


def test_resolve_engine_cloud_usable_when_env_key_set(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")
    dg = EngineEntry(name="dg", type="cloud", provider="deepgram")
    monkeypatch.setattr("resona_client.config.EngineConfig.load",
                        lambda: EngineConfig(engines=[dg]))
    result = resolve_engine(auto_start=False)
    assert result is dg


def test_resolve_engine_cloud_skipped_when_env_key_missing(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    dg = EngineEntry(name="dg", type="cloud", provider="deepgram")
    monkeypatch.setattr("resona_client.config.EngineConfig.load",
                        lambda: EngineConfig(engines=[dg]))
    assert resolve_engine(auto_start=False) is None


def test_resolve_engine_skips_missing_compose_dir_with_warning(monkeypatch, caplog, tmp_path):
    missing = tmp_path / "does-not-exist"
    entry = EngineEntry(name="c", api_url="http://c:7000", compose_dir=str(missing))
    monkeypatch.setattr("resona_client.config.EngineConfig.load",
                        lambda: EngineConfig(engines=[entry]))
    with patch("resona_client.config.is_reachable", return_value=False):
        result = resolve_engine(auto_start=True)
    assert result is None
    assert "compose_dir" in caplog.text
```

- [ ] 2. Run it. `uv run pytest packages/client/tests/test_config.py -k resolve_engine` — the new tests fail: `resolve_engine()` rejects `name=`/`private_only=` kwargs and raw-raises on a missing `compose_dir`.

- [ ] 3. Rewrite `resolve_engine()` in `packages/client/src/resona_client/config.py`:
```python
def resolve_engine(
    auto_start: bool = True,
    connect_timeout: float = 3.0,
    name: Optional[str] = None,
    private_only: bool = False,
) -> Optional[EngineEntry]:
    """Return a usable engine entry from ~/.resona/config.json.

    Args:
        auto_start: If True, try to start unreachable resona-api entries.
        connect_timeout: Per-entry reachability probe timeout.
        name: If given, resolve only the entry with this exact name.
        private_only: If True, skip entries where ``is_private()`` is False.

    Resolution:
      - ``cloud`` entries are usable when their API-key env var is set
        (no /health probe, no auto-start).
      - ``resona-api`` entries are usable when /health responds; if not and
        ``auto_start`` is set, an SSH tunnel or docker compose project is
        started. A configured ``compose_dir`` that does not exist is logged
        and skipped instead of raising FileNotFoundError.

    Returns None if no usable engine is found.
    """
    cfg = EngineConfig.load()
    engines = cfg.engines
    if name is not None:
        engines = [e for e in engines if e.name == name]
    if private_only:
        engines = [e for e in engines if e.is_private()]
    if not engines:
        return None

    # 1. Immediately usable entries.
    for entry in engines:
        if entry.type == "cloud":
            if entry.is_usable():
                return entry
        elif is_reachable(entry, timeout=connect_timeout):
            return entry

    if not auto_start:
        return None

    # 2. Auto-start resona-api entries (cloud entries cannot be started).
    for entry in engines:
        if entry.type == "cloud":
            continue
        if entry.ssh_host:
            if entry.name not in _active_tunnels:
                proc = _start_ssh_tunnel(entry)
                _active_tunnels[entry.name] = proc
            host = _local_host(entry.api_url)
            port = _local_port(entry.api_url)
            log.info(f"Waiting for SSH tunnel to '{entry.name}' (up to 30s)...")
            if _wait_for_port(host, port, timeout=30.0) and is_reachable(entry):
                return entry
        elif entry.compose_dir:
            if not Path(entry.compose_dir).is_dir():
                log.warning(
                    f"Engine '{entry.name}': compose_dir '{entry.compose_dir}' "
                    f"does not exist — skipping auto-start."
                )
                continue
            _start_compose(entry.compose_dir)
            log.info(f"Waiting for '{entry.name}' to become available (up to 120s)...")
            if _wait_for_engine(entry):
                return entry

    return None
```
(`_wait_for_engine` is the Plan-1-renamed `_wait_for_backend`; keep that name.)

- [ ] 4. Run the test. `uv run pytest packages/client/tests/test_config.py -k resolve_engine` — all passed.

- [ ] 5. Run the full config + client suite. `uv run pytest packages/client/` — all passed.

- [ ] 6. Commit:
```
git commit -am "feat(client): resolve_engine gains name/private_only, cloud usability, compose_dir hardening"
```

---

## Task 11 — `CloudEngine` in `resona_cli/engine.py`

**Files:**
- Modify: `apps/resona-cli/src/resona_cli/engine.py`
- Create: `apps/resona-cli/tests/test_cloud_engine.py`

Steps:

- [ ] 1. Write the failing test. Create `apps/resona-cli/tests/test_cloud_engine.py`:
```python
"""Tests for resona_cli.engine.CloudEngine."""
import io
import struct
import wave
from pathlib import Path
from unittest.mock import patch

import pytest

from resona_client.config import EngineEntry
from resona_cli.engine import CloudEngine
from resona_cloud_stt.errors import MissingAPIKeyError


def make_wav(path: Path) -> Path:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<" + "h" * 160, *([0] * 160)))
    path.write_bytes(buf.getvalue())
    return path


def test_cloud_engine_resolves_key_and_calls_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "secret-key")
    audio = make_wav(tmp_path / "a.wav")
    entry = EngineEntry(name="dg", type="cloud", provider="deepgram",
                        model="nova-3", options={"smart_format": True})
    engine = CloudEngine(entry)

    fake_result = {"text": "hi", "language": "de", "segments": []}
    with patch("resona_cloud_stt.providers.deepgram.transcribe",
               return_value=fake_result) as mock_tx:
        result = engine.transcribe(audio, language="de")

    assert result == fake_result
    _, kwargs = mock_tx.call_args
    assert kwargs["api_key"] == "secret-key"
    assert kwargs["model"] == "nova-3"
    assert kwargs["language"] == "de"
    assert kwargs["options"] == {"smart_format": True}


def test_cloud_engine_missing_key_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    audio = make_wav(tmp_path / "a.wav")
    entry = EngineEntry(name="dg", type="cloud", provider="deepgram")
    engine = CloudEngine(entry)
    with pytest.raises(MissingAPIKeyError, match="DEEPGRAM_API_KEY"):
        engine.transcribe(audio)


def test_cloud_engine_model_kwarg_overrides_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    audio = make_wav(tmp_path / "a.wav")
    entry = EngineEntry(name="oa", type="cloud", provider="openai", model="whisper-1")
    engine = CloudEngine(entry)
    with patch("resona_cloud_stt.providers.openai.transcribe",
               return_value={"text": "", "language": "", "segments": []}) as mock_tx:
        engine.transcribe(audio, model="gpt-4o-transcribe")
    assert mock_tx.call_args.kwargs["model"] == "gpt-4o-transcribe"
```

- [ ] 2. Run it. `uv run pytest apps/resona-cli/tests/test_cloud_engine.py` — fails: `ImportError: cannot import name 'CloudEngine'`.

- [ ] 3. Add `CloudEngine` to `apps/resona-cli/src/resona_cli/engine.py` (append after `InProcessEngine`):
```python
class CloudEngine:
    """Engine that transcribes via a cloud STT provider (resona-cloud-stt).

    Wraps a resolved ``cloud`` :class:`EngineEntry`. The provider's API key is
    read from the environment variable named in
    ``resona_cloud_stt.registry.PROVIDER_ENV_KEYS``. The ``transcribe``
    keyword arguments ``model`` and ``language`` override the entry's values
    for that run.
    """

    def __init__(self, entry) -> None:
        self._entry = entry

    def transcribe(self, audio: Path, **kwargs) -> TranscriptionResult:
        import os

        from resona_cloud_stt.errors import MissingAPIKeyError
        from resona_cloud_stt.registry import PROVIDER_ENV_KEYS, get_provider

        provider = self._entry.provider or ""
        env_var = PROVIDER_ENV_KEYS.get(provider)
        api_key = os.getenv(env_var) if env_var else None
        if not api_key:
            raise MissingAPIKeyError(env_var or f"<unknown provider {provider!r}>")

        model = kwargs.get("model") or self._entry.model
        language = kwargs.get("language") or None
        result = get_provider(provider).transcribe(
            Path(audio),
            api_key=api_key,
            model=model,
            language=language,
            options=self._entry.options or None,
        )
        return TranscriptionResult(
            text=result.get("text", ""),
            language=result.get("language", ""),
            segments=result.get("segments", []),
        )
```

- [ ] 4. Run the test. `uv run pytest apps/resona-cli/tests/test_cloud_engine.py` — all passed.

- [ ] 5. Commit:
```
git commit -am "feat(cli): add CloudEngine implementing the Engine protocol over resona-cloud-stt"
```

---

## Task 12 — CLI `engines list` merged view + built-in engine names

**Files:**
- Modify: `apps/resona-cli/src/resona_cli/engines.py`
- Modify: `apps/resona-cli/tests/test_engines.py`

Steps:

- [ ] 1. Write the failing test. Append to `apps/resona-cli/tests/test_engines.py`:
```python
# ── engines list — merged view ────────────────────────────────────────────────

def test_list_shows_builtin_local_engines_when_no_config(isolated_config):
    result = runner.invoke(app, ["engines", "list"])
    assert "faster-whisper" in result.output
    assert "whisper" in result.output
    assert "voxtral" in result.output
    assert "built-in" in result.output


def test_list_shows_config_entries_alongside_builtins(isolated_config):
    isolated_config.write_text(json.dumps({"engines": [
        {"name": "my-gpu-box", "api_url": "http://gpu:7000", "private": True},
        {"name": "deepgram", "type": "cloud", "provider": "deepgram"},
    ]}))
    with patch("resona_cli.engines.is_reachable", return_value=True):
        result = runner.invoke(app, ["engines", "list"])
    assert "my-gpu-box" in result.output
    assert "server" in result.output
    assert "deepgram" in result.output
    assert "cloud" in result.output


def test_list_marks_local_engines_private(isolated_config):
    result = runner.invoke(app, ["engines", "list"])
    # the three local engines are always private
    for line in result.output.splitlines():
        if "faster-whisper" in line:
            assert "yes" in line
```

- [ ] 2. Run it. `uv run pytest apps/resona-cli/tests/test_engines.py -k "builtin or merged or config_entries or marks_local"` — fails: `engines list` shows only config entries (no built-ins).

- [ ] 3. Add a built-in constant and rewrite `list` in `apps/resona-cli/src/resona_cli/engines.py`. At module top add:
```python
BUILTIN_ENGINES = ("faster-whisper", "whisper", "voxtral")
```
Replace the `list` command body:
```python
@engines_app.command("list")
def list_engines():
    """List built-in local engines plus configured server/cloud engines."""
    cfg = EngineConfig.load()
    typer.echo(f"  {'NAME':<18}{'TYPE':<9}{'PRIVATE':<9}STATUS")
    for name in BUILTIN_ENGINES:
        typer.echo(f"  {name:<18}{'local':<9}{'yes':<9}built-in")
    for e in cfg.engines:
        if e.type == "cloud":
            kind = "cloud"
            private = "no"
            status = "key set" if e.is_usable() else "no key"
        else:
            kind = "server"
            private = "yes" if e.is_private() else "no"
            status = "reachable" if is_reachable(e) else "unreachable"
        typer.echo(f"  {e.name:<18}{kind:<9}{private:<9}{status}")
```

- [ ] 4. Run the test. `uv run pytest apps/resona-cli/tests/test_engines.py -k "builtin or merged or config_entries or marks_local"` — all passed.

- [ ] 5. Run the whole engines test file. `uv run pytest apps/resona-cli/tests/test_engines.py` — all passed.

- [ ] 6. Commit:
```
git commit -am "feat(cli): engines list shows merged built-in + configured engine view"
```

---

## Task 13 — CLI `engines add` cloud options + collision rejection

**Files:**
- Modify: `apps/resona-cli/src/resona_cli/engines.py`
- Modify: `apps/resona-cli/tests/test_engines.py`

Steps:

- [ ] 1. Write the failing test. Append to `apps/resona-cli/tests/test_engines.py`:
```python
# ── engines add — cloud + collision ───────────────────────────────────────────

def test_add_cloud_engine(isolated_config):
    result = runner.invoke(app, [
        "engines", "add", "dg", "--type", "cloud", "--provider", "deepgram",
        "--model", "nova-3", "--option", "smart_format=true",
    ])
    assert result.exit_code == 0
    data = json.loads(isolated_config.read_text())
    entry = data["engines"][0]
    assert entry["type"] == "cloud"
    assert entry["provider"] == "deepgram"
    assert entry["model"] == "nova-3"
    assert entry["options"] == {"smart_format": "true"}


def test_add_cloud_engine_repeatable_option(isolated_config):
    result = runner.invoke(app, [
        "engines", "add", "dg", "--type", "cloud", "--provider", "deepgram",
        "--option", "smart_format=true", "--option", "diarize=false",
    ])
    assert result.exit_code == 0
    opts = json.loads(isolated_config.read_text())["engines"][0]["options"]
    assert opts == {"smart_format": "true", "diarize": "false"}


def test_add_cloud_engine_unknown_provider_rejected(isolated_config):
    result = runner.invoke(app, [
        "engines", "add", "bad", "--type", "cloud", "--provider", "nonsense",
    ])
    assert result.exit_code == 1
    assert "provider" in result.output.lower()


def test_add_private_resona_api_engine(isolated_config):
    with patch("resona_cli.engines.is_reachable", return_value=True):
        result = runner.invoke(app, [
            "engines", "add", "gpu", "http://gpu:7000", "--private",
        ])
    assert result.exit_code == 0
    assert json.loads(isolated_config.read_text())["engines"][0]["private"] is True


def test_add_rejects_name_shadowing_builtin_engine(isolated_config):
    result = runner.invoke(app, [
        "engines", "add", "whisper", "--type", "cloud", "--provider", "openai",
    ])
    assert result.exit_code == 1
    assert "built-in" in result.output.lower()


def test_add_option_bad_format_rejected(isolated_config):
    result = runner.invoke(app, [
        "engines", "add", "dg", "--type", "cloud", "--provider", "deepgram",
        "--option", "noequalsign",
    ])
    assert result.exit_code == 1
    assert "KEY=VALUE" in result.output
```

- [ ] 2. Run it. `uv run pytest apps/resona-cli/tests/test_engines.py -k "add_cloud or shadowing or add_private or option_bad"` — fails: `engines add` has no `--type`/`--provider`/`--model`/`--private`/`--option` options.

- [ ] 3. Rewrite the `add` command in `apps/resona-cli/src/resona_cli/engines.py`. `api_url` becomes optional (cloud entries have none):
```python
@engines_app.command("add")
def add_engine(
    name: str = typer.Argument(..., help="Unique name for this engine"),
    api_url: Optional[str] = typer.Argument(
        None, help="resona-api base URL (resona-api engines only)"),
    api_key: str = typer.Option("", "--key", "-k", help="API key (if the server requires one)"),
    compose_dir: Optional[str] = typer.Option(
        None, "--compose-dir", "-c",
        help="docker-compose project dir; enables auto-start (resona-api only)."),
    ssh_host: Optional[str] = typer.Option(
        None, "--ssh", "-s", help="SSH host to tunnel through (resona-api only)."),
    ssh_remote_port: Optional[int] = typer.Option(
        None, "--ssh-remote-port", help="Remote port on the SSH host."),
    engine_type: str = typer.Option(
        "resona-api", "--type", help="Engine type: resona-api or cloud."),
    provider: Optional[str] = typer.Option(
        None, "--provider", help="Cloud provider: deepgram, elevenlabs, openai."),
    model: Optional[str] = typer.Option(
        None, "--model", help="Provider model override (cloud engines)."),
    private: bool = typer.Option(
        False, "--private", help="Mark a resona-api engine as private."),
    option: list[str] = typer.Option(
        [], "--option", help="Provider option KEY=VALUE (repeatable; cloud engines)."),
):
    """Add a resona-api server engine or a cloud provider engine."""
    if name in BUILTIN_ENGINES:
        typer.echo(
            f"Error: '{name}' is a built-in local engine name and cannot be "
            f"used for a config entry.",
            err=True,
        )
        raise typer.Exit(1)

    options: dict = {}
    for item in option:
        if "=" not in item:
            typer.echo(f"Error: --option must be KEY=VALUE, got '{item}'", err=True)
            raise typer.Exit(1)
        key, value = item.split("=", 1)
        options[key] = value

    entry = EngineEntry(
        name=name,
        api_url=(api_url or "").rstrip("/"),
        api_key=api_key,
        compose_dir=compose_dir,
        ssh_host=ssh_host,
        ssh_remote_port=ssh_remote_port,
        type=engine_type,
        provider=provider,
        model=model,
        private=private,
        options=options,
    )

    cfg = EngineConfig.load()
    try:
        cfg.add(entry)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    if engine_type == "cloud":
        usable = entry.is_usable()
        status = "key set" if usable else "no API key in environment"
        typer.echo(f"Added cloud engine '{name}' ({provider}) — {status}")
    else:
        ok = is_reachable(entry)
        status = (typer.style("reachable", fg=typer.colors.GREEN)
                  if ok else typer.style("not reachable", fg=typer.colors.YELLOW))
        typer.echo(f"Added '{name}' ({api_url}) — {status}")
```

- [ ] 4. Run the test. `uv run pytest apps/resona-cli/tests/test_engines.py -k "add_cloud or shadowing or add_private or option_bad"` — all passed.

- [ ] 5. Run the whole engines test file. `uv run pytest apps/resona-cli/tests/test_engines.py` — all passed.

- [ ] 6. Commit:
```
git commit -am "feat(cli): engines add gains cloud options and rejects built-in name collisions"
```

---

## Task 14 — CLI `transcribe`: unified `--engine` resolution + routing

**Files:**
- Modify: `apps/resona-cli/src/resona_cli/transcribe.py`
- Modify: `apps/resona-cli/tests/test_transcribe.py`

Steps:

- [ ] 1. Write the failing test. Append to `apps/resona-cli/tests/test_transcribe.py`:
```python
# ── Unified --engine routing ──────────────────────────────────────────────────

from resona_client.config import EngineConfig, EngineEntry
from resona_cli.engines import BUILTIN_ENGINES  # noqa: F401  (sanity import)


def test_transcribe_engine_pins_cloud_entry(tmp_path, monkeypatch):
    """--engine NAME naming a cloud config entry routes to CloudEngine."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")
    make_wav(tmp_path / "a.wav")
    cfg = EngineConfig(engines=[
        EngineEntry(name="dg", type="cloud", provider="deepgram"),
    ])
    mock_engine = MagicMock()
    mock_engine.transcribe.return_value = {"text": "cloud text", "language": "de", "segments": []}

    with (
        patch("resona_client.config.EngineConfig.load", return_value=cfg),
        patch("resona_cli.transcribe.CloudEngine", return_value=mock_engine) as mock_cls,
        patch("resona_postprocess.sources.build_pipeline_from_config",
              return_value=_noop_pipeline()),
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path), "--engine", "dg"])

    assert result.exit_code == 0
    mock_cls.assert_called_once()
    mock_engine.transcribe.assert_called_once()


def test_transcribe_engine_pins_local_builtin(tmp_path):
    """--engine faster-whisper routes to the local fallback engine."""
    make_wav(tmp_path / "a.wav")
    mock_engine = _make_local_engine(transcript="local text")

    with (
        patch("resona_client.client.ResonaClient.from_config",
              side_effect=RuntimeError("no server")),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("x")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine) as mock_le,
        patch("resona_postprocess.sources.build_pipeline_from_config",
              return_value=_noop_pipeline()),
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path), "--engine", "whisper"])

    assert result.exit_code == 0
    assert mock_le.call_args.kwargs.get("engine") == "whisper"


def test_transcribe_engine_unknown_name_errors(tmp_path):
    make_wav(tmp_path / "a.wav")
    with patch("resona_client.config.EngineConfig.load", return_value=EngineConfig()):
        result = runner.invoke(app, ["transcribe", str(tmp_path), "--engine", "ghost"])
    assert result.exit_code != 0
    assert "ghost" in result.output


def test_transcribe_engine_pins_resona_api_entry(tmp_path):
    """--engine NAME naming a resona-api entry routes to ResonaClient."""
    make_wav(tmp_path / "a.wav")
    cfg = EngineConfig(engines=[EngineEntry(name="srv", api_url="http://srv:7000")])
    mock_client = make_client()

    with (
        patch("resona_client.config.EngineConfig.load", return_value=cfg),
        patch("resona_cli.transcribe.ResonaClient", return_value=mock_client),
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path), "--engine", "srv"])

    assert result.exit_code == 0
    mock_client.submit_job.assert_called_once()
```

- [ ] 2. Run it. `uv run pytest apps/resona-cli/tests/test_transcribe.py -k "engine_pins or engine_unknown"` — fails: `transcribe` only has the (Plan-1-renamed) `--engine` flag meaning "local engine plugin"; it does not resolve config entries or route to `CloudEngine`.

- [ ] 3. Rewrite `transcribe.py`. Replace the imports block and `transcribe_files` to add a resolver and routing. Keep `_expand_inputs`, `_transcribe_local_fallback`, `_resolve_local_engine` unchanged except where noted (the local fallback's keyword stays `engine=` per Plan 1).

Add at the top of `transcribe.py`:
```python
from .engine import CloudEngine, InProcessEngine
from resona_client.client import ResonaClient
from resona_client.config import EngineConfig, resolve_engine
from .engines import BUILTIN_ENGINES
```
(Remove the now-duplicated lazy imports of `ResonaClient`/`EngineConfig` inside the function body.)

Replace `transcribe_files`:
```python
def transcribe_files(
    inputs: list[str] = typer.Argument(
        ..., help="Audio files, glob patterns, or directories.", metavar="INPUTS..."),
    recursive: bool = typer.Option(False, "--recursive", "-r",
        help="Recurse into directories / use `**` in glob patterns."),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir",
        help="Directory to write transcripts."),
    model: Optional[str] = typer.Option(None, "--model",
        help="Model name override (local fallback and cloud engines)."),
    language: str = typer.Option("de", "--language",
        help="Language hint for transcription."),
    engine_timeout: float = typer.Option(120.0, "--engine-timeout",
        help="Seconds to wait for local engine startup (local fallback only)."),
    engine: Optional[str] = typer.Option(None, "--engine",
        help="Engine name: a built-in local engine, or a config.json server/cloud entry."),
    private: Optional[bool] = typer.Option(None, "--private/--no-private",
        help="Require a private engine. Defaults to default_private in config.json."),
):
    """Transcribe audio files. Accepts files, glob patterns, or directories."""
    cfg = EngineConfig.load()
    want_private = cfg.default_private if private is None else private
    files = _expand_inputs(inputs, recursive=recursive)
    if not files:
        print("No audio files found.")
        return

    target = _resolve_target(engine, cfg, want_private)
    if target is None:
        return  # _resolve_target already printed the error

    kind, value = target
    if kind == "cloud":
        _transcribe_cloud(files, output_dir, value, model, language)
    elif kind == "resona-api":
        _transcribe_via_client(files, output_dir, value, model)
    else:  # kind == "local"
        _transcribe_local_fallback(files, output_dir, model, language,
                                   engine_timeout, value)
```

Add the resolver and the two new routing helpers:
```python
def _resolve_target(engine, cfg, want_private):
    """Resolve --engine into ('cloud'|'resona-api'|'local', payload).

    Returns None (after printing an error) when resolution fails.
    """
    if engine is not None:
        entry = cfg.get(engine)
        if entry is not None:
            if want_private and not entry.is_private():
                typer.echo(
                    f"Engine '{engine}' is not private — refused under --private",
                    err=True,
                )
                raise typer.Exit(1)
            return (entry.type, entry)
        if engine in BUILTIN_ENGINES:
            return ("local", engine)
        typer.echo(f"Unknown engine '{engine}'.", err=True)
        raise typer.Exit(1)

    # No --engine: try config entries by priority, then a local engine.
    entry = resolve_engine(private_only=want_private)
    if entry is not None:
        return (entry.type, entry)
    return ("local", cfg.default_engine)


def _transcribe_cloud(files, output_dir, entry, model, language):
    from resona_postprocess.sources import build_pipeline_from_config
    from resona_cloud_stt.errors import CloudSTTError

    cloud = CloudEngine(entry)
    pipeline = build_pipeline_from_config()
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    typer.echo(f"Transcribing via cloud engine '{entry.name}' ({entry.provider}).",
               err=True)
    for filepath in files:
        try:
            kwargs = {"language": language}
            if model is not None:
                kwargs["model"] = model
            result = cloud.transcribe(filepath, **kwargs)
            transcript = pipeline.run(result.get("text", ""))
            out_path = (output_dir or filepath.parent) / f"{filepath.stem}.txt"
            out_path.write_text(transcript, encoding="utf-8")
            print(f"Transcribed {filepath.name} -> {out_path}")
        except CloudSTTError as e:
            typer.echo(f"Failed to transcribe {filepath.name}: {e}", err=True)


def _transcribe_via_client(files, output_dir, entry, model):
    if model is not None:
        typer.echo("--model is ignored when submitting to a resona-api server.",
                   err=True)
    client = ResonaClient(base_url=entry.api_url, api_key=entry.api_key)
    _submit_and_wait(client, files, output_dir)
```

Extract the existing submit/wait loop (lines that submit jobs and write outputs in the old `transcribe_files`) into `_submit_and_wait(client, files, output_dir)` so both the explicit-entry path and the no-`--engine` resona-api path share it. When `--engine` is not given and no config entry resolves, the `("local", cfg.default_engine)` branch runs `_transcribe_local_fallback` exactly as today.

- [ ] 4. Run the test. `uv run pytest apps/resona-cli/tests/test_transcribe.py` — all passed (existing fallback tests still pass; `--engine`/`--private` ones now pass).

- [ ] 5. Commit:
```
git commit -am "feat(cli): unified --engine selector routing to cloud/server/local in transcribe"
```

---

## Task 15 — CLI `--private` semantics, refusal error, `default_private`

**Files:**
- Modify: `apps/resona-cli/tests/test_transcribe.py`
- (No source change expected — `_resolve_target` from Task 14 already implements this; this task adds the explicit behavioral tests and fixes any gaps found.)

Steps:

- [ ] 1. Write the failing test. Append to `apps/resona-cli/tests/test_transcribe.py`:
```python
# ── --private semantics ───────────────────────────────────────────────────────

def test_private_refuses_explicit_non_private_engine(tmp_path, monkeypatch):
    """--engine naming a non-private cloud entry under --private is a hard error."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")
    make_wav(tmp_path / "a.wav")
    cfg = EngineConfig(engines=[EngineEntry(name="dg", type="cloud", provider="deepgram")])

    with (
        patch("resona_client.config.EngineConfig.load", return_value=cfg),
        patch("resona_cli.transcribe.CloudEngine") as mock_cls,
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path),
                                     "--engine", "dg", "--private"])

    assert result.exit_code == 1
    assert "not private" in result.output
    mock_cls.assert_not_called()  # refused before any upload


def test_private_allows_explicit_private_resona_api_engine(tmp_path):
    make_wav(tmp_path / "a.wav")
    cfg = EngineConfig(engines=[
        EngineEntry(name="gpu", api_url="http://gpu:7000", private=True),
    ])
    mock_client = make_client()
    with (
        patch("resona_client.config.EngineConfig.load", return_value=cfg),
        patch("resona_cli.transcribe.ResonaClient", return_value=mock_client),
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path),
                                     "--engine", "gpu", "--private"])
    assert result.exit_code == 0
    mock_client.submit_job.assert_called_once()


def test_private_falls_through_to_local_engine(tmp_path):
    """--private with no usable private config entry falls through to local."""
    make_wav(tmp_path / "a.wav")
    cfg = EngineConfig(engines=[
        EngineEntry(name="dg", type="cloud", provider="deepgram"),
    ])
    mock_engine = _make_local_engine(transcript="local")
    with (
        patch("resona_client.config.EngineConfig.load", return_value=cfg),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("x")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine),
        patch("resona_postprocess.sources.build_pipeline_from_config",
              return_value=_noop_pipeline()),
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path), "--private"])
    assert result.exit_code == 0
    mock_engine.transcribe.assert_called_once()


def test_default_private_makes_private_implicit(tmp_path, monkeypatch):
    """default_private=true refuses an explicit non-private engine without --private."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")
    make_wav(tmp_path / "a.wav")
    cfg = EngineConfig(
        engines=[EngineEntry(name="dg", type="cloud", provider="deepgram")],
        default_private=True,
    )
    with (
        patch("resona_client.config.EngineConfig.load", return_value=cfg),
        patch("resona_cli.transcribe.CloudEngine") as mock_cls,
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path), "--engine", "dg"])
    assert result.exit_code == 1
    assert "not private" in result.output
    mock_cls.assert_not_called()


def test_no_private_overrides_default_private(tmp_path, monkeypatch):
    """--no-private lets a non-private engine run even when default_private=true."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")
    make_wav(tmp_path / "a.wav")
    cfg = EngineConfig(
        engines=[EngineEntry(name="dg", type="cloud", provider="deepgram")],
        default_private=True,
    )
    mock_engine = MagicMock()
    mock_engine.transcribe.return_value = {"text": "ok", "language": "de", "segments": []}
    with (
        patch("resona_client.config.EngineConfig.load", return_value=cfg),
        patch("resona_cli.transcribe.CloudEngine", return_value=mock_engine),
        patch("resona_postprocess.sources.build_pipeline_from_config",
              return_value=_noop_pipeline()),
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path),
                                     "--engine", "dg", "--no-private"])
    assert result.exit_code == 0
    mock_engine.transcribe.assert_called_once()
```

- [ ] 2. Run it. `uv run pytest apps/resona-cli/tests/test_transcribe.py -k "private"` — confirm which pass. The refusal-before-upload, fall-through, `default_private`, and `--no-private` cases should pass from Task 14's `_resolve_target`; if any fail, fix `_resolve_target` minimally (e.g. ensure the `want_private` check runs before `CloudEngine` is constructed).

- [ ] 3. Run the test again to confirm all pass. `uv run pytest apps/resona-cli/tests/test_transcribe.py -k "private"` — all passed.

- [ ] 4. Run the whole CLI suite. `uv run pytest apps/resona-cli/` — all passed.

- [ ] 5. Commit:
```
git commit -am "test(cli): cover --private refusal, default_private, and --no-private override"
```

---

## Task 16 — resona-api: route jobs to `resona-cloud-stt`

**Files:**
- Modify: `packages/api/src/resona_api/tasks_transcribe.py`
- Create: `packages/api/tests/test_cloud_routing.py`

Steps:

- [ ] 1. Write the failing test. Create `packages/api/tests/test_cloud_routing.py`:
```python
"""Tests for cloud-engine routing in resona_api.tasks_transcribe."""
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch

from sqlmodel import Session

from resona_api.db.engine import engine
from resona_api.db.models import Job, JobStatus
from resona_api.db.utils import register_job
from resona_api.engine_client import EngineClient
from resona_api.tasks_transcribe import TranscribeTask
from test_tasks import FILE_PATH, write_audio_file


def make_task(engine_client=None) -> TranscribeTask:
    if engine_client is None:
        engine_client = MagicMock(spec=EngineClient)
    return TranscribeTask(shutdown_event=Event(), engine_client=engine_client)


def test_routes_to_cloud_when_cloud_engine_env_set(monkeypatch):
    result = register_job("cloud1.wav", "cloud1.wav")
    job_id = result["id"]
    write_audio_file("cloud1.wav")

    cloud_result = {"text": "cloud transcript", "language": "de", "segments": []}
    mock_provider = MagicMock()
    mock_provider.transcribe.return_value = cloud_result

    engine_client = MagicMock(spec=EngineClient)  # must NOT be used
    task = make_task(engine_client=engine_client)

    with (
        patch("resona_api.tasks_transcribe.config",
              side_effect=lambda key, default=None: {
                  "RESONA_CLOUD_ENGINE": "deepgram",
                  "RESONA_CLOUD_MODEL": "nova-3",
                  "RESONA_CLOUD_OPTIONS": "",
                  "DEEPGRAM_API_KEY": "k",
              }.get(key, default)),
        patch("resona_api.tasks_transcribe.get_cloud_provider", return_value=mock_provider),
        patch("resona_api.tasks_transcribe.write_md_file"),
        patch("resona_api.tasks_transcribe.get_active_replacements", return_value=[]),
        patch("resona_api.tasks_transcribe.get_active_initial_prompts_string", return_value=""),
    ):
        task._process_next_job()

    engine_client.transcribe.assert_not_called()
    mock_provider.transcribe.assert_called_once()
    _, kwargs = mock_provider.transcribe.call_args
    assert kwargs["api_key"] == "k"
    assert kwargs["model"] == "nova-3"

    with Session(engine) as session:
        job = session.get(Job, job_id)
    assert job.status == JobStatus.COMPLETED
    assert job.transcript == "cloud transcript"


def test_default_path_uses_engine_client_when_cloud_engine_unset(monkeypatch):
    result = register_job("local1.wav", "local1.wav")
    job_id = result["id"]
    write_audio_file("local1.wav")

    engine_client = MagicMock(spec=EngineClient)
    engine_client.transcribe.return_value = {"text": "via engine", "language": "de", "segments": []}
    task = make_task(engine_client=engine_client)

    with (
        patch("resona_api.tasks_transcribe.config",
              side_effect=lambda key, default=None: default),
        patch("resona_api.tasks_transcribe.write_md_file"),
        patch("resona_api.tasks_transcribe.update_job_attributes_from_result"),
        patch("resona_api.tasks_transcribe.get_active_replacements", return_value=[]),
        patch("resona_api.tasks_transcribe.get_active_initial_prompts_string", return_value=""),
    ):
        task._process_next_job()

    engine_client.transcribe.assert_called_once()
    with Session(engine) as session:
        job = session.get(Job, job_id)
    assert job.status == JobStatus.COMPLETED
```

- [ ] 2. Run it. `uv run pytest packages/api/tests/test_cloud_routing.py` — fails: `tasks_transcribe` has no `get_cloud_provider` and always calls `EngineClient`.

- [ ] 3. Modify `packages/api/src/resona_api/tasks_transcribe.py`. Add a helper and a routing branch. Add near the top (after the existing imports):
```python
import json as _json


def get_cloud_provider(name: str):
    """Return the resona-cloud-stt provider module for ``name``.

    Wrapper so tests can patch this symbol without importing the package.
    """
    from resona_cloud_stt.registry import get_provider
    return get_provider(name)


def _cloud_transcribe(filepath: Path) -> dict:
    """Transcribe via a cloud provider selected by RESONA_CLOUD_* env vars."""
    from resona_cloud_stt.errors import MissingAPIKeyError
    from resona_cloud_stt.registry import PROVIDER_ENV_KEYS

    provider_name = config("RESONA_CLOUD_ENGINE")
    env_var = PROVIDER_ENV_KEYS.get(provider_name)
    api_key = config(env_var, default="") if env_var else ""
    if not api_key:
        raise MissingAPIKeyError(env_var or provider_name)

    model = config("RESONA_CLOUD_MODEL", default=None)
    options_raw = config("RESONA_CLOUD_OPTIONS", default="")
    options = _json.loads(options_raw) if options_raw else None

    provider = get_cloud_provider(provider_name)
    return provider.transcribe(
        Path(filepath),
        api_key=api_key,
        model=model,
        language="de",
        options=options,
    )
```
Inside `_process_next_job`, replace the `asr_result = self.engine_client.transcribe(...)` call with:
```python
                if config("RESONA_CLOUD_ENGINE", default=""):
                    asr_result = _cloud_transcribe(filepath)
                else:
                    asr_result = self.engine_client.transcribe(
                        filepath=filepath,
                        language="de",
                        initial_prompt=initial_prompt,
                        task="translate" if job.translate else "transcribe",
                    )
```
The postprocessing block (`get_active_replacements` → `PostprocessPipeline` → `job.md`) stays exactly as it is — unchanged.

- [ ] 4. Run the test. `uv run pytest packages/api/tests/test_cloud_routing.py` — all passed.

- [ ] 5. Run the full api suite to confirm the default path still works. `uv run pytest packages/api/` — all passed.

- [ ] 6. Commit:
```
git commit -am "feat(api): route jobs to resona-cloud-stt when RESONA_CLOUD_ENGINE is set"
```

---

## Task 17 — Full-suite verification + docs

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`

Steps:

- [ ] 1. Run the entire test suite. `uv run pytest` — all passed (existing count + the new cloud-stt, cloud-engine, cloud-routing, config, and CLI tests).

- [ ] 2. Update `CLAUDE.md`: add `packages/cloud-stt/` to the workspace layout tree; add a `resona-cloud-stt` row to the package-responsibilities section describing `types.py`/`errors.py`/`registry.py`/`providers/*`; add the cloud env-var keys (`DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`, `OPENAI_API_KEY`) and the `RESONA_CLOUD_ENGINE` / `RESONA_CLOUD_MODEL` / `RESONA_CLOUD_OPTIONS` rows to the environment-variables table; document the unified `--engine` flag, `--private`/`--no-private`, and `default_private`.

- [ ] 3. Update `README.md`: document the three cloud engines and their API-key env vars; show `resona engines add <name> --type cloud --provider deepgram`; show `resona transcribe ./audio/ --engine deepgram` and `resona transcribe ./audio/ --private`; add `resona-cloud-stt` to the package table.

- [ ] 4. Verify docs build (no broken references). `uv run mkdocs build`.

- [ ] 5. Commit:
```
git commit -am "docs: document cloud engines, --engine/--private flags, and resona-cloud-stt"
```

---

## Notes for the implementing worker

- **Provider `transcribe` signature is invariant:** `transcribe(audio_path: Path, *, api_key: str, model: str | None = None, language: str | None = None, options: dict | None = None) -> TranscriptionResult`. It is identical in `deepgram.py`, `elevenlabs.py`, `openai.py`, every test, `CloudEngine`, and `_cloud_transcribe`.
- **The cloud package never reads env vars.** Callers (`CloudEngine`, `_cloud_transcribe`) resolve the key and pass it as `api_key=`.
- **Unknown `options` keys** are dropped with `logging.warning` inside each provider's `_filter_options` — never forwarded.
- **Suite stays green after every commit.** If a task's test depends on a module added by a later task, gate it with `pytest.mark.xfail(strict=True)` (only Task 3 → 4 needs this) and remove the marker in the task that satisfies it.
- DRY: the submit-and-wait loop is extracted once (`_submit_and_wait`) and reused. YAGNI: no streaming, no keyring, no cloud entry points — see the spec's "Out of scope".
