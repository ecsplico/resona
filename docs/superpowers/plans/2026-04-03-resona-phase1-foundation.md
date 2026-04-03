# Resona Phase 1: Foundation — Engine Core + Postprocess + Faster-Whisper Backend

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the three foundational packages (resona-engine-core, resona-postprocess, resona-engine-faster-whisper) with formal interfaces, entry-point discovery, and LLM postprocessing — all working end-to-end with the existing ws-api and ws-cli.

**Architecture:** Extract the transcriber protocol and FastAPI app into resona-engine-core. Move replacements + add LLM postprocessing into resona-postprocess. Create resona-engine-faster-whisper as the first backend package using entry-point discovery. The old ws-engine package stays temporarily as a compatibility shim until Phase 2 migrates consumers.

**Tech Stack:** Python 3.12, FastAPI, faster-whisper, litellm, hatchling, uv workspaces, Python entry points

**Spec:** `docs/superpowers/specs/2026-04-03-resona-architecture-redesign.md`

**Phases overview:**
- **Phase 1 (this plan):** engine-core + postprocess + faster-whisper backend — standalone, testable
- **Phase 2:** Rename ws-api → resona-api, ws-client → resona-client, ws-cli → resona-cli, integrate postprocess
- **Phase 3:** Additional backends (voxtral, whisper), Docker images, docs, cleanup old packages

---

## File Structure

### New packages to create

```
packages/engine-core/
├── pyproject.toml
└── src/resona_engine_core/
    ├── __init__.py
    ├── protocol.py          ← Transcriber Protocol + TranscriptionResult
    ├── registry.py          ← entry-point discovery + singleton
    ├── audio.py             ← load_audio() extracted from ws_engine.utils
    ├── app.py               ← FastAPI app (copied from ws_engine.app, adapted)
    ├── run.py               ← uvicorn entry point
    ├── auth.py              ← API key auth (copied from ws_engine.auth, renamed env var)
    ├── ws_transcribe.py     ← WebSocket streaming (adapted imports)
    ├── ws_live.py           ← WebSocket live (adapted imports)
    └── live_transcriber.py  ← VAD + local agreement (adapted imports)

packages/engine-faster-whisper/
├── pyproject.toml
└── src/resona_engine_faster_whisper/
    ├── __init__.py
    └── transcriber.py       ← FastWhisperTranscriber (from ws_engine.transcriber_fast_whisper)

packages/postprocess/
├── pyproject.toml
└── src/resona_postprocess/
    ├── __init__.py
    ├── replacements.py      ← apply_replacements() moved from ws_engine.replacements
    ├── llm.py               ← llm_postprocess() via litellm
    ├── pipeline.py          ← PostprocessPipeline composable chain
    └── sources.py           ← load from JSON file, build pipeline from config
```

### New test files

```
packages/engine-core/tests/
├── test_protocol.py
├── test_registry.py
├── test_audio.py
├── test_app.py
└── test_auth.py

packages/engine-faster-whisper/tests/
└── test_transcriber.py

packages/postprocess/tests/
├── test_replacements.py
├── test_llm.py
├── test_pipeline.py
└── test_sources.py
```

### Files to modify

```
pyproject.toml               ← add new workspace members
```

---

## Task 1: Create resona-engine-core package scaffold

**Files:**
- Create: `packages/engine-core/pyproject.toml`
- Create: `packages/engine-core/src/resona_engine_core/__init__.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "resona-engine-core"
version = "0.1.0"
description = "Core FastAPI app, transcriber protocol, and audio utilities for Resona"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.7",
    "uvicorn>=0.34.0",
    "numpy>=2.1.3",
    "ffmpeg-python>=0.2.0",
    "python-decouple>=3.8",
    "python-multipart>=0.0.20",
    "websockets>=16.0",
]

[project.scripts]
resona-engine-core = "resona_engine_core.run:main"

[tool.hatch.build.targets.wheel]
packages = ["src/resona_engine_core"]
```

- [ ] **Step 2: Create __init__.py**

```python
```

(Empty file)

- [ ] **Step 3: Add to workspace**

Edit `pyproject.toml` (root) — the `members` line under `[tool.uv.workspace]`:

```toml
[tool.uv.workspace]
members = ["packages/*", "apps/cli"]
```

This already uses `packages/*` glob, so the new `packages/engine-core/` is auto-discovered. No change needed. Verify:

Run: `uv sync --all-packages --dry-run 2>&1 | head -20`
Expected: should list `resona-engine-core` among resolved packages

- [ ] **Step 4: Commit**

```bash
git add packages/engine-core/
git commit -m "feat: scaffold resona-engine-core package"
```

---

## Task 2: Transcriber Protocol and TranscriptionResult

**Files:**
- Create: `packages/engine-core/src/resona_engine_core/protocol.py`
- Create: `packages/engine-core/tests/test_protocol.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/engine-core/tests/test_protocol.py
import numpy as np
from resona_engine_core.protocol import Transcriber, TranscriptionResult


class _DummyTranscriber:
    """Minimal implementation to verify the protocol."""

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        language: str = "de",
        task: str = "transcribe",
        initial_prompt: str | None = None,
        word_timestamps: bool = False,
        vad_filter: bool = False,
        **kwargs,
    ) -> TranscriptionResult:
        return TranscriptionResult(text="hello", language="en", segments=[])


class _BadTranscriber:
    """Missing transcribe method."""
    pass


def test_dummy_satisfies_protocol():
    t = _DummyTranscriber()
    assert isinstance(t, Transcriber)


def test_bad_transcriber_fails_protocol():
    t = _BadTranscriber()
    assert not isinstance(t, Transcriber)


def test_transcription_result_is_typed_dict():
    r = TranscriptionResult(text="hi", language="de", segments=[])
    assert r["text"] == "hi"
    assert r["language"] == "de"
    assert r["segments"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/engine-core/tests/test_protocol.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write the implementation**

```python
# packages/engine-core/src/resona_engine_core/protocol.py
"""Transcriber protocol — all backends implement this interface."""

from typing import Protocol, TypedDict, runtime_checkable

import numpy as np


class TranscriptionResult(TypedDict):
    """Return type for all transcriber backends."""

    text: str
    language: str
    segments: list[dict]


@runtime_checkable
class Transcriber(Protocol):
    """Protocol that every Resona transcription backend must satisfy.

    Backends must accept ``**kwargs`` for forward compatibility.
    """

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        language: str = "de",
        task: str = "transcribe",
        initial_prompt: str | None = None,
        word_timestamps: bool = False,
        vad_filter: bool = False,
        **kwargs,
    ) -> TranscriptionResult: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/engine-core/tests/test_protocol.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add packages/engine-core/src/resona_engine_core/protocol.py packages/engine-core/tests/test_protocol.py
git commit -m "feat(engine-core): add Transcriber protocol and TranscriptionResult"
```

---

## Task 3: Audio loading utility

**Files:**
- Create: `packages/engine-core/src/resona_engine_core/audio.py`
- Create: `packages/engine-core/tests/test_audio.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/engine-core/tests/test_audio.py
import io
import struct
import numpy as np
from unittest.mock import patch, MagicMock
from resona_engine_core.audio import load_audio, SAMPLE_RATE


def _make_wav_bytes(n_samples: int = 160, sr: int = 16000) -> bytes:
    """Create minimal valid WAV bytes (PCM 16-bit mono)."""
    data = struct.pack(f"<{n_samples}h", *([1000] * n_samples))
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(data), b"WAVE",
        b"fmt ", 16, 1, 1, sr, sr * 2, 2, 16,
        b"data", len(data),
    )
    return header + data


def test_sample_rate_constant():
    assert SAMPLE_RATE == 16000


def test_load_audio_returns_float32(tmp_path):
    """load_audio with encode=False on raw PCM int16 bytes."""
    raw = struct.pack("<4h", 0, 16384, -16384, 0)
    f = io.BytesIO(raw)
    audio = load_audio(f, encode=False, sr=16000)
    assert audio.dtype == np.float32
    assert len(audio) == 4


@patch("resona_engine_core.audio.ffmpeg")
def test_load_audio_calls_ffmpeg(mock_ffmpeg):
    """load_audio with encode=True invokes ffmpeg."""
    pcm = struct.pack("<2h", 0, 16384)
    mock_ffmpeg.input.return_value.output.return_value.run.return_value = (pcm, b"")
    audio = load_audio(io.BytesIO(b"fake"), encode=True, sr=16000)
    assert audio.dtype == np.float32
    mock_ffmpeg.input.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/engine-core/tests/test_audio.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write the implementation**

Extract from `packages/ws-engine/src/ws_engine/utils.py`, keeping only audio loading:

```python
# packages/engine-core/src/resona_engine_core/audio.py
"""Audio loading utilities for Resona engine."""

import logging
from typing import BinaryIO

import ffmpeg
import numpy as np

SAMPLE_RATE = 16000
log = logging.getLogger(__name__)


def load_audio(file: BinaryIO, encode: bool = True, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Read an audio file object as mono float32 waveform, resampling as necessary.

    Args:
        file: File-like object containing audio data.
        encode: If True, use ffmpeg to decode/resample. If False, read raw PCM int16.
        sr: Target sample rate.

    Returns:
        numpy float32 array normalised to [-1, 1].
    """
    if encode:
        try:
            out, _ = (
                ffmpeg.input("pipe:", threads=0)
                .output("-", format="s16le", acodec="pcm_s16le", ac=1, ar=sr)
                .run(cmd="ffmpeg", capture_stdout=True, capture_stderr=True, input=file.read())
            )
        except ffmpeg.Error as e:
            stderr = e.stderr.decode()
            log.error(f"FFmpeg error during audio loading: {stderr}")
            raise RuntimeError(f"Failed to load audio: {stderr}") from e
        except Exception as e:
            log.error(f"Unexpected error during audio loading: {e}")
            raise RuntimeError(f"Failed to load audio: {e}") from e
    else:
        out = file.read()

    waveform = np.frombuffer(out, np.int16).flatten().astype(np.float32) / 32768.0
    log.info(f"Audio loaded successfully. Shape: {waveform.shape}, Sample Rate: {sr}")
    return waveform
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/engine-core/tests/test_audio.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add packages/engine-core/src/resona_engine_core/audio.py packages/engine-core/tests/test_audio.py
git commit -m "feat(engine-core): add audio loading utility"
```

---

## Task 4: Backend registry with entry-point discovery

**Files:**
- Create: `packages/engine-core/src/resona_engine_core/registry.py`
- Create: `packages/engine-core/tests/test_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/engine-core/tests/test_registry.py
from unittest.mock import patch, MagicMock
import numpy as np
import pytest

from resona_engine_core.protocol import Transcriber, TranscriptionResult
from resona_engine_core.registry import get_transcriber, _load_from_entrypoint, reset


class FakeTranscriber:
    def __init__(self, device: str = "cpu", modelname: str | None = None):
        self.device = device

    def transcribe(
        self, audio: np.ndarray, *, language="de", task="transcribe",
        initial_prompt=None, word_timestamps=False, vad_filter=False, **kwargs
    ) -> TranscriptionResult:
        return TranscriptionResult(text="test", language="de", segments=[])


def _make_entry_point(name: str, cls):
    ep = MagicMock()
    ep.name = name
    ep.load.return_value = cls
    return ep


def setup_function():
    """Reset singleton before each test."""
    reset()


@patch("resona_engine_core.registry.entry_points")
@patch("resona_engine_core.registry.config")
def test_load_from_entrypoint_finds_backend(mock_config, mock_eps):
    mock_config.return_value = "fake"
    mock_eps.return_value = [_make_entry_point("fake", FakeTranscriber)]
    t = _load_from_entrypoint()
    assert isinstance(t, Transcriber)
    assert t.device == "cpu"


@patch("resona_engine_core.registry.entry_points")
@patch("resona_engine_core.registry.config")
def test_load_from_entrypoint_raises_on_missing(mock_config, mock_eps):
    mock_config.return_value = "nonexistent"
    mock_eps.return_value = [_make_entry_point("fake", FakeTranscriber)]
    with pytest.raises(ValueError, match="not found"):
        _load_from_entrypoint()


@patch("resona_engine_core.registry.entry_points")
@patch("resona_engine_core.registry.config")
def test_get_transcriber_is_singleton(mock_config, mock_eps):
    mock_config.return_value = "fake"
    mock_eps.return_value = [_make_entry_point("fake", FakeTranscriber)]
    t1 = get_transcriber()
    t2 = get_transcriber()
    assert t1 is t2


@patch("resona_engine_core.registry.entry_points")
@patch("resona_engine_core.registry.config")
def test_explicit_backend_name(mock_config, mock_eps):
    mock_eps.return_value = [_make_entry_point("specific", FakeTranscriber)]
    t = _load_from_entrypoint(backend="specific")
    assert isinstance(t, Transcriber)
    mock_config.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/engine-core/tests/test_registry.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write the implementation**

```python
# packages/engine-core/src/resona_engine_core/registry.py
"""Backend discovery and singleton management via Python entry points."""

import logging
from importlib.metadata import entry_points
from threading import Lock

from decouple import config

from .protocol import Transcriber

log = logging.getLogger(__name__)

_transcriber: Transcriber | None = None
_init_lock = Lock()

ENTRY_POINT_GROUP = "resona.backends"


def _detect_device() -> str:
    """Return 'cuda' if available, else 'cpu'."""
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def _load_from_entrypoint(backend: str | None = None) -> Transcriber:
    """Discover and instantiate a transcriber backend by name.

    Resolution order:
      1. Explicit ``backend`` argument
      2. ``RESONA_BACKEND`` env var
    """
    name = backend or config("RESONA_BACKEND", default="faster-whisper")
    eps = entry_points(group=ENTRY_POINT_GROUP)
    for ep in eps:
        if ep.name == name:
            cls = ep.load()
            device = _detect_device()
            log.info(f"Loading backend '{name}' on {device}")
            instance = cls(device=device)
            assert isinstance(instance, Transcriber), (
                f"Backend '{name}' does not satisfy the Transcriber protocol"
            )
            log.info(f"Backend '{name}' ready.")
            return instance

    installed = [ep.name for ep in eps]
    raise ValueError(
        f"Backend '{name}' not found. Installed backends: {installed}"
    )


def get_transcriber(backend: str | None = None) -> Transcriber:
    """Return the singleton transcriber, creating it on first call.

    Thread-safe via double-checked locking.
    """
    global _transcriber
    if _transcriber is None:
        with _init_lock:
            if _transcriber is None:
                _transcriber = _load_from_entrypoint(backend)
    return _transcriber


def reset() -> None:
    """Reset the singleton (for testing only)."""
    global _transcriber
    _transcriber = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/engine-core/tests/test_registry.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add packages/engine-core/src/resona_engine_core/registry.py packages/engine-core/tests/test_registry.py
git commit -m "feat(engine-core): add entry-point backend registry with singleton"
```

---

## Task 5: Auth module

**Files:**
- Create: `packages/engine-core/src/resona_engine_core/auth.py`
- Create: `packages/engine-core/tests/test_auth.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/engine-core/tests/test_auth.py
import pytest
from unittest.mock import patch
from fastapi import HTTPException
from resona_engine_core.auth import verify_api_key


@pytest.mark.asyncio
@patch("resona_engine_core.auth.config", return_value=None)
async def test_auth_disabled_when_no_key(mock_config):
    result = await verify_api_key(api_key=None)
    assert result is None


@pytest.mark.asyncio
@patch("resona_engine_core.auth.config", return_value="secret")
async def test_valid_key_passes(mock_config):
    result = await verify_api_key(api_key="secret")
    assert result == "secret"


@pytest.mark.asyncio
@patch("resona_engine_core.auth.config", return_value="secret")
async def test_missing_key_raises_401(mock_config):
    with pytest.raises(HTTPException) as exc_info:
        await verify_api_key(api_key=None)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
@patch("resona_engine_core.auth.config", return_value="secret")
async def test_wrong_key_raises_401(mock_config):
    with pytest.raises(HTTPException) as exc_info:
        await verify_api_key(api_key="wrong")
    assert exc_info.value.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/engine-core/tests/test_auth.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write the implementation**

Adapted from `ws_engine.auth` with renamed env var:

```python
# packages/engine-core/src/resona_engine_core/auth.py
"""API key authentication for Resona engine."""

import logging
from typing import Optional

from decouple import config
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

log = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(
    api_key: Optional[str] = Security(api_key_header),
) -> Optional[str]:
    """Validate API key. If RESONA_ENGINE_KEY is not set, auth is disabled."""
    expected = config("RESONA_ENGINE_KEY", default=None)

    if not expected:
        return None

    if api_key is None:
        log.warning("Engine API request without API key")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    if api_key != expected:
        log.warning("Invalid engine API key attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return api_key
```

- [ ] **Step 4: Add pytest-asyncio to dev deps, then run tests**

First, add `pytest-asyncio` to the root `pyproject.toml` dev dependency group:

```toml
[dependency-groups]
dev = [
    "httpx>=0.28.1",
    "pytest>=9.0.2",
    "pytest-asyncio>=0.24.0",
    "respx>=0.21.1",
    "mkdocs-material>=9.5",
    "mkdocstrings[python]>=0.25",
]
```

Run: `uv sync --all-packages --no-build-isolation-package openai-whisper && uv run pytest packages/engine-core/tests/test_auth.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add packages/engine-core/src/resona_engine_core/auth.py packages/engine-core/tests/test_auth.py
git commit -m "feat(engine-core): add API key auth with RESONA_ENGINE_KEY"
```

---

## Task 6: FastAPI app, WebSocket handlers, live transcriber, and run module

**Files:**
- Create: `packages/engine-core/src/resona_engine_core/app.py`
- Create: `packages/engine-core/src/resona_engine_core/run.py`
- Create: `packages/engine-core/src/resona_engine_core/ws_transcribe.py`
- Create: `packages/engine-core/src/resona_engine_core/ws_live.py`
- Create: `packages/engine-core/src/resona_engine_core/live_transcriber.py`
- Create: `packages/engine-core/tests/test_app.py`

These are largely adapted from ws-engine with import path changes. The key difference: **no replacements in the engine** — the `/transcribe` endpoint returns raw text only.

- [ ] **Step 1: Create live_transcriber.py**

Copy `packages/ws-engine/src/ws_engine/live_transcriber.py` to `packages/engine-core/src/resona_engine_core/live_transcriber.py`. Change one import:

```python
# Old:
from .transcriber_factory import getTranscriber
# New:
from .registry import get_transcriber
```

And update the call site at `self._transcriber = getTranscriber()` to `self._transcriber = get_transcriber()`.

- [ ] **Step 2: Create ws_transcribe.py**

Copy `packages/ws-engine/src/ws_engine/ws_transcribe.py` to `packages/engine-core/src/resona_engine_core/ws_transcribe.py`. Change imports:

```python
# Old:
import whisper
from .transcriber_factory import getTranscriber
# New:
from .registry import get_transcriber
```

Replace `transcriber = getTranscriber()` with `transcriber = get_transcriber()`. Remove the `import whisper` line (not used in this file).

- [ ] **Step 3: Create ws_live.py**

Copy `packages/ws-engine/src/ws_engine/ws_live.py` to `packages/engine-core/src/resona_engine_core/ws_live.py`. Change import:

```python
# Old:
from .live_transcriber import LiveTranscriber, SAMPLE_RATE
# New (unchanged — same relative import within engine-core):
from .live_transcriber import LiveTranscriber, SAMPLE_RATE
```

No changes needed — the relative import is the same within the new package.

- [ ] **Step 4: Create app.py**

Adapted from `ws_engine.app` — **removes replacements handling from /transcribe**:

```python
# packages/engine-core/src/resona_engine_core/app.py
"""
resona-engine-core: Stateless FastAPI transcription service.

Endpoints:
  GET  /health
  POST /transcribe
  WS   /ws/transcribe
  WS   /ws/live
"""
import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from typing import Optional

from decouple import config
from fastapi import FastAPI, File, Form, UploadFile, WebSocket, Depends
from fastapi.middleware.cors import CORSMiddleware

from .auth import verify_api_key
from .registry import get_transcriber
from .audio import load_audio, SAMPLE_RATE
from .ws_transcribe import transcribe_websocket
from .ws_live import live_transcribe_websocket

log = logging.getLogger(__name__)

_model_lock = threading.Lock()


def _run_asr(file, task: str = "transcribe", language: str = "de", **asr_options) -> dict:
    """Load audio and run transcription using the registered backend."""
    if isinstance(file, str):
        with open(file, "rb") as f:
            audio = load_audio(f, sr=SAMPLE_RATE)
    else:
        audio = load_audio(file, sr=SAMPLE_RATE)

    options = {"task": task, "language": language, **asr_options}

    with _model_lock:
        transcriber = get_transcriber()
        result = transcriber.transcribe(audio, **options)

    return result


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Pre-load the ASR model before accepting requests."""
    log.info("Pre-loading ASR model at startup...")
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, get_transcriber)
        log.info("ASR model loaded and ready.")
    except Exception as e:
        log.warning(f"Model pre-load failed ({e}) — will load on first request.")
    yield


app = FastAPI(
    title="resona-engine",
    description="Stateless transcription engine",
    lifespan=lifespan,
)

CORS_ORIGINS = config("CORS_ORIGINS", default="*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/transcribe")
async def transcribe(
    audio_file: UploadFile = File(...),
    task: str = Form(default="transcribe"),
    language: str = Form(default="de"),
    initial_prompt: Optional[str] = Form(default=None),
    output: Optional[str] = Form(default=None),
    vad_filter: bool = Form(default=False),
    word_timestamps: bool = Form(default=False),
    api_key: Optional[str] = Depends(verify_api_key),
):
    """Transcribe an audio file. Returns raw text, language, and segments.

    No replacements are applied — postprocessing is caller-side.
    """
    asr_options = {
        "vad_filter": vad_filter,
        "word_timestamps": word_timestamps,
    }
    if initial_prompt:
        asr_options["initial_prompt"] = initial_prompt

    result = _run_asr(audio_file.file, task=task, language=language, **asr_options)

    raw_segments = result.get("segments", [])
    serializable_segments = []
    for seg in raw_segments:
        if isinstance(seg, dict):
            serializable_segments.append(seg)
        else:
            try:
                d = {"start": seg.start, "end": seg.end, "text": seg.text}
                if word_timestamps and hasattr(seg, "words") and seg.words:
                    d["words"] = [
                        {"word": w.word, "start": w.start, "end": w.end}
                        for w in seg.words
                    ]
                serializable_segments.append(d)
            except AttributeError:
                serializable_segments.append({"text": str(seg)})

    return {
        "text": result.get("text", ""),
        "language": result.get("language", language),
        "segments": serializable_segments,
    }


@app.websocket("/ws/transcribe")
async def websocket_transcribe_endpoint(websocket: WebSocket):
    try:
        await transcribe_websocket(websocket)
    except Exception as e:
        log.error(f"WebSocket error: {e}", exc_info=True)
        raise


@app.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket):
    try:
        await live_transcribe_websocket(websocket)
    except Exception as e:
        log.error(f"Live WebSocket error: {e}", exc_info=True)
        raise
```

- [ ] **Step 5: Create run.py**

```python
# packages/engine-core/src/resona_engine_core/run.py
import uvicorn
from decouple import config
from .app import app

loglevel = config("LOGLEVEL", default="info")
port: int = config("PORT", default=7001, cast=int)


def main():
    """Entry point for resona-engine commands."""
    uvicorn.run(app, host="0.0.0.0", port=port, log_level=loglevel)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Write test for the /transcribe and /health endpoints**

```python
# packages/engine-core/tests/test_app.py
import io
import struct
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from resona_engine_core.protocol import TranscriptionResult


@pytest.fixture
def mock_transcriber():
    t = MagicMock()
    t.transcribe.return_value = TranscriptionResult(
        text="hello world",
        language="en",
        segments=[{"start": 0.0, "end": 1.0, "text": "hello world"}],
    )
    return t


@pytest.fixture
def client(mock_transcriber):
    with patch("resona_engine_core.app.get_transcriber", return_value=mock_transcriber):
        with patch("resona_engine_core.auth.config", return_value=None):
            from resona_engine_core.app import app
            yield TestClient(app)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_transcribe_returns_text(client, mock_transcriber):
    raw_pcm = struct.pack("<4h", 0, 16384, -16384, 0)
    with patch("resona_engine_core.app.load_audio", return_value=__import__("numpy").zeros(16000)):
        resp = client.post(
            "/transcribe",
            files={"audio_file": ("test.wav", io.BytesIO(raw_pcm), "audio/wav")},
            data={"language": "en"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "hello world"
    assert body["language"] == "en"
    assert "md" not in body  # No replacements in engine


def test_transcribe_no_md_field(client, mock_transcriber):
    """Engine should never return an 'md' field — postprocessing is caller-side."""
    with patch("resona_engine_core.app.load_audio", return_value=__import__("numpy").zeros(16000)):
        resp = client.post(
            "/transcribe",
            files={"audio_file": ("test.wav", io.BytesIO(b"\x00" * 100), "audio/wav")},
        )
    assert "md" not in resp.json()
```

- [ ] **Step 7: Run tests**

Run: `uv run pytest packages/engine-core/tests/test_app.py -v`
Expected: 3 passed

- [ ] **Step 8: Commit**

```bash
git add packages/engine-core/src/resona_engine_core/
git add packages/engine-core/tests/test_app.py
git commit -m "feat(engine-core): add FastAPI app, WS handlers, live transcriber, run module"
```

---

## Task 7: Create resona-engine-faster-whisper backend package

**Files:**
- Create: `packages/engine-faster-whisper/pyproject.toml`
- Create: `packages/engine-faster-whisper/src/resona_engine_faster_whisper/__init__.py`
- Create: `packages/engine-faster-whisper/src/resona_engine_faster_whisper/transcriber.py`
- Create: `packages/engine-faster-whisper/tests/test_transcriber.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/engine-faster-whisper/tests/test_transcriber.py
from unittest.mock import patch, MagicMock
import numpy as np
from resona_engine_core.protocol import Transcriber
from resona_engine_faster_whisper.transcriber import FastWhisperTranscriber


def _mock_segment(text="hello", start=0.0, end=1.0):
    seg = MagicMock()
    seg.text = text
    seg.start = start
    seg.end = end
    seg.words = None
    return seg


@patch("resona_engine_faster_whisper.transcriber.WhisperModel")
def test_satisfies_protocol(mock_model_cls):
    t = FastWhisperTranscriber(device="cpu", modelname="tiny")
    assert isinstance(t, Transcriber)


@patch("resona_engine_faster_whisper.transcriber.WhisperModel")
def test_transcribe_returns_expected_keys(mock_model_cls):
    mock_model = mock_model_cls.return_value
    info = MagicMock()
    info.language = "de"
    mock_model.transcribe.return_value = (iter([_mock_segment()]), info)

    t = FastWhisperTranscriber(device="cpu", modelname="tiny")
    result = t.transcribe(np.zeros(16000, dtype=np.float32), language="de")

    assert "text" in result
    assert "language" in result
    assert "segments" in result
    assert result["language"] == "de"
    assert "hello" in result["text"]


@patch("resona_engine_faster_whisper.transcriber.WhisperModel")
def test_initial_prompt_passed_through(mock_model_cls):
    mock_model = mock_model_cls.return_value
    info = MagicMock()
    info.language = "de"
    mock_model.transcribe.return_value = (iter([]), info)

    t = FastWhisperTranscriber(device="cpu", modelname="tiny")
    t.transcribe(np.zeros(16000), initial_prompt="test prompt")

    call_kwargs = mock_model.transcribe.call_args
    assert call_kwargs[1].get("initial_prompt") == "test prompt" or \
           call_kwargs.kwargs.get("initial_prompt") == "test prompt"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/engine-faster-whisper/tests/test_transcriber.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "resona-engine-faster-whisper"
version = "0.1.0"
description = "Faster-whisper (CTranslate2) backend for Resona"
requires-python = ">=3.12"
dependencies = [
    "resona-engine-core",
    "faster-whisper>=1.1.1",
    "torch>=2.11.0.dev",
]

[project.entry-points."resona.backends"]
faster-whisper = "resona_engine_faster_whisper.transcriber:FastWhisperTranscriber"

[project.scripts]
resona-engine-faster-whisper = "resona_engine_core.run:main"

[tool.hatch.build.targets.wheel]
packages = ["src/resona_engine_faster_whisper"]
```

- [ ] **Step 4: Create __init__.py**

Empty file: `packages/engine-faster-whisper/src/resona_engine_faster_whisper/__init__.py`

- [ ] **Step 5: Write the transcriber implementation**

```python
# packages/engine-faster-whisper/src/resona_engine_faster_whisper/transcriber.py
"""Faster-whisper (CTranslate2) transcription backend for Resona."""

import logging

import numpy as np
from decouple import config
from faster_whisper import WhisperModel

from resona_engine_core.protocol import TranscriptionResult

log = logging.getLogger(__name__)

DEFAULT_MODEL: str = config("DEFAULT_FASTWHISPER_MODEL", default="large-v3")


class FastWhisperTranscriber:
    """CTranslate2-based Whisper backend. Default and recommended.

    Uses INT8-quantised models for fast inference with lower memory.
    """

    def __init__(self, device: str = "cpu", modelname: str | None = None):
        model_name = modelname or DEFAULT_MODEL
        compute_type = "int8_float16" if device == "cuda" else "int8"
        log.info(f"Loading FastWhisper model: {model_name} on {device} ({compute_type})")
        self.model = WhisperModel(model_name, device=device, compute_type=compute_type)

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        language: str = "de",
        task: str = "transcribe",
        initial_prompt: str | None = None,
        word_timestamps: bool = False,
        vad_filter: bool = False,
        **kwargs,
    ) -> TranscriptionResult:
        opts = {
            "language": language,
            "task": task,
            "word_timestamps": word_timestamps,
            "vad_filter": vad_filter,
            "beam_size": kwargs.get("beam_size", 5),
            **{k: v for k, v in kwargs.items() if k != "beam_size"},
        }
        if initial_prompt:
            opts["initial_prompt"] = initial_prompt

        segment_gen, info = self.model.transcribe(audio, **opts)
        segments = list(segment_gen)
        text = "".join(seg.text for seg in segments)

        return TranscriptionResult(
            text=text,
            language=info.language,
            segments=segments,
        )
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest packages/engine-faster-whisper/tests/test_transcriber.py -v`
Expected: 3 passed

- [ ] **Step 7: Commit**

```bash
git add packages/engine-faster-whisper/
git commit -m "feat: add resona-engine-faster-whisper backend package"
```

---

## Task 8: Create resona-postprocess package — replacements

**Files:**
- Create: `packages/postprocess/pyproject.toml`
- Create: `packages/postprocess/src/resona_postprocess/__init__.py`
- Create: `packages/postprocess/src/resona_postprocess/replacements.py`
- Create: `packages/postprocess/tests/test_replacements.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/postprocess/tests/test_replacements.py
from resona_postprocess.replacements import apply_replacements


def test_simple_replacement():
    text = "hello world"
    rules = [{"name": "hello", "replacement": "goodbye"}]
    assert apply_replacements(text, rules) == "goodbye world"


def test_case_insensitive():
    text = "Hello World"
    rules = [{"name": "hello", "replacement": "goodbye"}]
    assert apply_replacements(text, rules) == "goodbye World"


def test_regex_pattern():
    text = "Dr. Smith arrived"
    rules = [{"name": r"\bDr\.", "replacement": "Doctor"}]
    assert apply_replacements(text, rules) == "Doctor Smith arrived"


def test_multiple_replacements_in_order():
    text = "foo bar baz"
    rules = [
        {"name": "foo", "replacement": "AAA"},
        {"name": "bar", "replacement": "BBB"},
    ]
    assert apply_replacements(text, rules) == "AAA BBB baz"


def test_invalid_regex_skipped():
    text = "hello world"
    rules = [{"name": "[invalid", "replacement": "x"}]
    result = apply_replacements(text, rules)
    assert result == "hello world"


def test_empty_replacements():
    assert apply_replacements("hello", []) == "hello"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/postprocess/tests/test_replacements.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "resona-postprocess"
version = "0.1.0"
description = "Postprocessing pipeline for Resona — static replacements and LLM"
requires-python = ">=3.12"
dependencies = [
    "litellm>=1.30.0",
    "python-decouple>=3.8",
]

[tool.hatch.build.targets.wheel]
packages = ["src/resona_postprocess"]
```

- [ ] **Step 4: Create __init__.py and replacements.py**

```python
# packages/postprocess/src/resona_postprocess/__init__.py
```

(Empty)

```python
# packages/postprocess/src/resona_postprocess/replacements.py
"""Static regex-based text replacements."""

import re
import logging

log = logging.getLogger(__name__)


def apply_replacements(text: str, replacements: list[dict[str, str]]) -> str:
    """Apply a list of regex replacements to text in order.

    Each replacement dict must have 'name' (regex pattern) and 'replacement' (text).
    Invalid patterns are logged and skipped.
    """
    for r in replacements:
        try:
            new_text, n = re.compile(r["name"], re.IGNORECASE).subn(r["replacement"], text)
            if n > 0:
                text = new_text
        except re.error as e:
            log.warning(f"Invalid replacement pattern '{r.get('name')}': {e}")
    return text
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest packages/postprocess/tests/test_replacements.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add packages/postprocess/
git commit -m "feat(postprocess): add static regex replacements"
```

---

## Task 9: Postprocess pipeline

**Files:**
- Create: `packages/postprocess/src/resona_postprocess/pipeline.py`
- Create: `packages/postprocess/tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/postprocess/tests/test_pipeline.py
from resona_postprocess.pipeline import PostprocessPipeline


def test_empty_pipeline_is_noop():
    p = PostprocessPipeline()
    assert p.run("hello") == "hello"


def test_single_step():
    p = PostprocessPipeline()
    p.add("upper", str.upper)
    assert p.run("hello") == "HELLO"


def test_chained_steps_run_in_order():
    p = PostprocessPipeline()
    p.add("prefix", lambda t: f"[{t}]")
    p.add("upper", str.upper)
    assert p.run("hello") == "[HELLO]"


def test_add_returns_self():
    p = PostprocessPipeline()
    result = p.add("noop", lambda t: t)
    assert result is p


def test_fluent_api():
    result = (
        PostprocessPipeline()
        .add("exclaim", lambda t: t + "!")
        .add("upper", str.upper)
        .run("hello")
    )
    assert result == "HELLO!"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/postprocess/tests/test_pipeline.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write the implementation**

```python
# packages/postprocess/src/resona_postprocess/pipeline.py
"""Composable postprocessing pipeline — chain of str → str transformations."""

from __future__ import annotations

from typing import Callable

PostprocessStep = Callable[[str], str]


class PostprocessPipeline:
    """Ordered chain of text postprocessing steps."""

    def __init__(self) -> None:
        self._steps: list[tuple[str, PostprocessStep]] = []

    def add(self, name: str, step: PostprocessStep) -> PostprocessPipeline:
        """Append a named step. Returns self for fluent chaining."""
        self._steps.append((name, step))
        return self

    def run(self, text: str) -> str:
        """Run all steps in order, returning the final text."""
        for _name, step in self._steps:
            text = step(text)
        return text
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest packages/postprocess/tests/test_pipeline.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add packages/postprocess/src/resona_postprocess/pipeline.py packages/postprocess/tests/test_pipeline.py
git commit -m "feat(postprocess): add composable PostprocessPipeline"
```

---

## Task 10: LLM postprocessing via litellm

**Files:**
- Create: `packages/postprocess/src/resona_postprocess/llm.py`
- Create: `packages/postprocess/tests/test_llm.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/postprocess/tests/test_llm.py
from unittest.mock import patch, MagicMock
from resona_postprocess.llm import llm_postprocess


@patch("resona_postprocess.llm.litellm")
@patch("resona_postprocess.llm.config")
def test_calls_litellm_completion(mock_config, mock_litellm):
    mock_config.side_effect = lambda key, default="": {
        "RESONA_LLM_MODEL": "gpt-4o-mini",
        "RESONA_LLM_API_BASE": "",
    }.get(key, default)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "formatted text"
    mock_litellm.completion.return_value = mock_response

    result = llm_postprocess("raw text", prompt="Format this.")
    assert result == "formatted text"
    mock_litellm.completion.assert_called_once()

    call_kwargs = mock_litellm.completion.call_args
    messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "Format this."
    assert messages[1]["content"] == "raw text"


@patch("resona_postprocess.llm.litellm")
@patch("resona_postprocess.llm.config")
def test_explicit_model_overrides_env(mock_config, mock_litellm):
    mock_config.side_effect = lambda key, default="": default

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "ok"
    mock_litellm.completion.return_value = mock_response

    llm_postprocess("text", prompt="p", model="ollama/llama3")

    call_kwargs = mock_litellm.completion.call_args
    assert call_kwargs.kwargs.get("model") or call_kwargs[1].get("model") == "ollama/llama3"


@patch("resona_postprocess.llm.litellm")
@patch("resona_postprocess.llm.config")
def test_api_base_passed_when_set(mock_config, mock_litellm):
    mock_config.side_effect = lambda key, default="": {
        "RESONA_LLM_MODEL": "gpt-4o-mini",
        "RESONA_LLM_API_BASE": "http://localhost:11434",
    }.get(key, default)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "ok"
    mock_litellm.completion.return_value = mock_response

    llm_postprocess("text", prompt="p")

    call_kwargs = mock_litellm.completion.call_args
    assert call_kwargs.kwargs.get("api_base") == "http://localhost:11434"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/postprocess/tests/test_llm.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write the implementation**

```python
# packages/postprocess/src/resona_postprocess/llm.py
"""LLM-based postprocessing via litellm."""

import logging

import litellm
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest packages/postprocess/tests/test_llm.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add packages/postprocess/src/resona_postprocess/llm.py packages/postprocess/tests/test_llm.py
git commit -m "feat(postprocess): add LLM postprocessing via litellm"
```

---

## Task 11: Config sources and pipeline builder

**Files:**
- Create: `packages/postprocess/src/resona_postprocess/sources.py`
- Create: `packages/postprocess/tests/test_sources.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/postprocess/tests/test_sources.py
import json
from pathlib import Path
from unittest.mock import patch

from resona_postprocess.sources import (
    load_replacements_from_file,
    build_pipeline_from_config,
)
from resona_postprocess.pipeline import PostprocessPipeline


def test_load_replacements_from_missing_file(tmp_path):
    result = load_replacements_from_file(tmp_path / "nonexistent.json")
    assert result == []


def test_load_replacements_from_file(tmp_path):
    f = tmp_path / "replacements.json"
    f.write_text(json.dumps([
        {"name": "foo", "replacement": "bar"},
    ]))
    result = load_replacements_from_file(f)
    assert len(result) == 1
    assert result[0]["name"] == "foo"


def test_build_pipeline_no_config(tmp_path):
    pipeline = build_pipeline_from_config(tmp_path / "nonexistent.json")
    assert isinstance(pipeline, PostprocessPipeline)
    assert pipeline.run("hello") == "hello"


def test_build_pipeline_replacements_only(tmp_path):
    replacements = tmp_path / "replacements.json"
    replacements.write_text(json.dumps([{"name": "hello", "replacement": "goodbye"}]))

    config = tmp_path / "postprocess.json"
    config.write_text(json.dumps({
        "steps": [
            {"type": "replacements", "source": str(replacements)},
        ]
    }))

    pipeline = build_pipeline_from_config(config)
    assert pipeline.run("hello world") == "goodbye world"


@patch("resona_postprocess.sources.llm_postprocess", return_value="LLM OUTPUT")
def test_build_pipeline_with_llm_step(mock_llm, tmp_path):
    config = tmp_path / "postprocess.json"
    config.write_text(json.dumps({
        "steps": [
            {
                "type": "llm",
                "name": "format",
                "prompt": "Format this text.",
                "model": "ollama/llama3",
            },
        ]
    }))

    pipeline = build_pipeline_from_config(config)
    result = pipeline.run("raw text")
    assert result == "LLM OUTPUT"
    mock_llm.assert_called_once_with("raw text", prompt="Format this text.", model="ollama/llama3")


def test_build_pipeline_fallback_to_replacements_json(tmp_path):
    """When postprocess.json is missing, fall back to replacements.json."""
    replacements = tmp_path / "replacements.json"
    replacements.write_text(json.dumps([{"name": "foo", "replacement": "bar"}]))

    pipeline = build_pipeline_from_config(
        config_path=tmp_path / "postprocess.json",
        replacements_fallback=replacements,
    )
    assert pipeline.run("foo baz") == "bar baz"


def test_relative_source_resolved_to_config_dir(tmp_path):
    """Source paths in postprocess.json resolve relative to the config directory."""
    replacements = tmp_path / "replacements.json"
    replacements.write_text(json.dumps([{"name": "a", "replacement": "b"}]))

    config = tmp_path / "postprocess.json"
    config.write_text(json.dumps({
        "steps": [
            {"type": "replacements", "source": "replacements.json"},
        ]
    }))

    pipeline = build_pipeline_from_config(config)
    assert pipeline.run("a c") == "b c"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/postprocess/tests/test_sources.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write the implementation**

```python
# packages/postprocess/src/resona_postprocess/sources.py
"""Load postprocessing config from files and build pipelines."""

import json
import logging
from pathlib import Path

from .llm import llm_postprocess
from .pipeline import PostprocessPipeline
from .replacements import apply_replacements

log = logging.getLogger(__name__)

_DEFAULT_CONFIG_DIR = Path.home() / ".resona"


def load_replacements_from_file(path: Path | None = None) -> list[dict[str, str]]:
    """Load replacement rules from a JSON file.

    Returns an empty list if the file doesn't exist.
    """
    path = path or (_DEFAULT_CONFIG_DIR / "replacements.json")
    if not path.exists():
        return []
    return json.loads(path.read_text())


def build_pipeline_from_config(
    config_path: Path | None = None,
    replacements_fallback: Path | None = None,
) -> PostprocessPipeline:
    """Build a PostprocessPipeline from a config file.

    If config_path doesn't exist, falls back to a bare replacements file.
    Relative source paths in the config resolve relative to the config directory.

    Args:
        config_path: Path to postprocess.json. Defaults to ~/.resona/postprocess.json.
        replacements_fallback: Path to replacements.json for fallback.
            Defaults to ~/.resona/replacements.json.
    """
    config_path = config_path or (_DEFAULT_CONFIG_DIR / "postprocess.json")
    config_dir = config_path.parent

    if not config_path.exists():
        fallback = replacements_fallback or (config_dir / "replacements.json")
        rules = load_replacements_from_file(fallback)
        pipeline = PostprocessPipeline()
        if rules:
            pipeline.add("replacements", lambda t, r=rules: apply_replacements(t, r))
        return pipeline

    cfg = json.loads(config_path.read_text())
    pipeline = PostprocessPipeline()

    for step in cfg.get("steps", []):
        step_type = step["type"]

        if step_type == "replacements":
            source = step.get("source")
            if source:
                source_path = Path(source)
                if not source_path.is_absolute():
                    source_path = config_dir / source_path
            else:
                source_path = None
            rules = load_replacements_from_file(source_path)
            pipeline.add("replacements", lambda t, r=rules: apply_replacements(t, r))

        elif step_type == "llm":
            prompt = step["prompt"]
            model = step.get("model")
            pipeline.add(
                step.get("name", "llm"),
                lambda t, p=prompt, m=model: llm_postprocess(t, prompt=p, model=m),
            )

        else:
            log.warning(f"Unknown postprocess step type: {step_type}")

    return pipeline
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest packages/postprocess/tests/test_sources.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add packages/postprocess/src/resona_postprocess/sources.py packages/postprocess/tests/test_sources.py
git commit -m "feat(postprocess): add config sources and pipeline builder"
```

---

## Task 12: Integration test — full stack with entry points

**Files:**
- Create: `packages/engine-faster-whisper/tests/test_integration.py`

This test verifies that the entry-point wiring works end-to-end: engine-core discovers faster-whisper backend, loads it, and the FastAPI app serves transcriptions.

- [ ] **Step 1: Write the integration test**

```python
# packages/engine-faster-whisper/tests/test_integration.py
"""Integration test: engine-core + faster-whisper backend via entry points."""
from unittest.mock import patch, MagicMock
from importlib.metadata import EntryPoint

import numpy as np
from fastapi.testclient import TestClient

from resona_engine_core.protocol import TranscriptionResult


def _fake_entry_point():
    """Create a mock entry point that returns a mock transcriber."""
    from resona_engine_core.protocol import Transcriber

    class MockTranscriber:
        def __init__(self, device="cpu", modelname=None):
            pass

        def transcribe(self, audio, *, language="de", task="transcribe",
                       initial_prompt=None, word_timestamps=False,
                       vad_filter=False, **kwargs):
            return TranscriptionResult(
                text="integration test",
                language=language,
                segments=[],
            )

    ep = MagicMock()
    ep.name = "faster-whisper"
    ep.load.return_value = MockTranscriber
    return [ep]


@patch("resona_engine_core.registry.entry_points", side_effect=_fake_entry_point)
@patch("resona_engine_core.registry.config", return_value="faster-whisper")
@patch("resona_engine_core.auth.config", return_value=None)
def test_full_stack_transcribe(mock_auth_config, mock_reg_config, mock_eps):
    from resona_engine_core.registry import reset
    reset()

    from resona_engine_core.app import app
    client = TestClient(app)

    with patch("resona_engine_core.app.load_audio", return_value=np.zeros(16000)):
        resp = client.post(
            "/transcribe",
            files={"audio_file": ("test.wav", b"\x00" * 100, "audio/wav")},
            data={"language": "en"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "integration test"
    assert "md" not in body

    reset()
```

- [ ] **Step 2: Run test**

Run: `uv run pytest packages/engine-faster-whisper/tests/test_integration.py -v`
Expected: 1 passed

- [ ] **Step 3: Commit**

```bash
git add packages/engine-faster-whisper/tests/test_integration.py
git commit -m "test: add full-stack integration test for engine-core + faster-whisper"
```

---

## Task 13: Verify uv workspace resolution and run all tests

- [ ] **Step 1: Sync all packages**

Run: `uv sync --all-packages --no-build-isolation-package openai-whisper`
Expected: resolves all new packages without errors

- [ ] **Step 2: Run all new package tests**

Run: `uv run pytest packages/engine-core/tests/ packages/engine-faster-whisper/tests/ packages/postprocess/tests/ -v`
Expected: all tests pass

- [ ] **Step 3: Run existing tests to verify no regressions**

Run: `uv run pytest packages/ws-engine/tests/ packages/ws-api/tests/ packages/ws-client/tests/ apps/cli/tests/ -v`
Expected: all existing tests still pass

- [ ] **Step 4: Commit any fixes needed**

If tests fail, fix and commit with descriptive message.

---

## Summary

After Phase 1, you have:
- **resona-engine-core** — formal Transcriber Protocol, entry-point registry, FastAPI app (no replacements)
- **resona-engine-faster-whisper** — first backend, registered via entry point, runnable as standalone service
- **resona-postprocess** — static replacements + LLM via litellm + composable pipeline + JSON config

The old ws-engine package is untouched and still works. Phase 2 will migrate ws-api, ws-client, and ws-cli to use the new packages, and Phase 3 adds remaining backends, Docker images, and docs.
