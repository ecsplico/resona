# Adding a New Engine

An engine is a Python package that wraps an ASR backend and exposes it through
the `Transcriber` protocol. The same FastAPI server (`resona-engine-server`)
runs for every engine — only the backend class changes.

---

## The engine contract

Before writing any code, understand what an engine is and is not responsible for.

| The engine MUST | The engine must NOT |
|---|---|
| Accept `audio: np.ndarray` (16 kHz, float32, mono) | Access the database |
| Return `TranscriptionResult(text, language, segments)` | Apply replacements or postprocessing |
| Accept `language`, `task`, `initial_prompt`, `word_timestamps`, `vad_filter` | Delete audio files |
| Be stateless — every call is self-contained | Make outbound HTTP calls |
| Implement `__init__(self, device: str, modelname: str \| None = None)` | |

Postprocessing is the caller's responsibility (resona-api or the CLI's local
pipeline). The engine returns raw ASR output only.

---

## Step-by-step guide

### Step 1 — Create the package directory

```
packages/engine-myengine/
└── src/
    └── resona_engine_myengine/
        ├── __init__.py
        └── transcriber.py
```

Follow the src-layout used by all other engine packages. The module name must
be `resona_engine_<name>` where `<name>` matches the entry-point key you will
register in step 4.

### Step 2 — Implement the transcriber class

```python
# packages/engine-myengine/src/resona_engine_myengine/transcriber.py

import logging
import numpy as np
from decouple import config

from resona_asr_core.protocol import TranscriptionResult

log = logging.getLogger(__name__)

DEFAULT_MODEL: str = config("DEFAULT_MYENGINE_MODEL", default="my-model-v1")


class MyEngineTranscriber:
    """My custom ASR backend."""

    def __init__(self, device: str = "cpu", modelname: str | None = None):
        model_name = modelname or DEFAULT_MODEL
        log.info(f"Loading MyEngine model: {model_name} on {device}")
        # initialise the backend here
        self.model = ...

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
        # call the backend
        result = self.model.run(audio, language=language, task=task)

        return TranscriptionResult(
            text=result.text,
            language=result.language,
            segments=result.segments,
        )
```

Key points:

- Import `TranscriptionResult` from `resona_asr_core.protocol`, not defined locally.
- Read model configuration via `config()` from `python-decouple` — never use `os.environ[]`.
- The constructor signature `(self, device, modelname)` is fixed — the registry calls it this way.
- Accept `**kwargs` in `transcribe` for forward compatibility.

### Step 3 — Write `pyproject.toml`

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "resona-engine-myengine"
version = "0.1.0"
description = "My custom ASR backend for Resona"
requires-python = ">=3.12"
dependencies = [
    "resona-asr-core",
    "resona-engine-server",
    "my-asr-library>=1.0",
]

[tool.uv.sources]
resona-asr-core = { workspace = true }
resona-engine-server = { workspace = true }

[project.entry-points."resona.engines"]
my-engine = "resona_engine_myengine.transcriber:MyEngineTranscriber"

[project.scripts]
resona-engine-myengine = "resona_engine_server.run:main"

[tool.hatch.build.targets.wheel]
packages = ["src/resona_engine_myengine"]
```

Critical fields:

- `[project.entry-points."resona.engines"]` — the key (`my-engine`) is the value
  `RESONA_ENGINE` must be set to. It is also what appears in `resona engines status`.
- `[project.scripts]` — the script name is the command users run to start this
  engine. It always points to `resona_engine_server.run:main` — the shared FastAPI app.
- `[tool.uv.sources]` — both workspace deps must be declared here.

### Step 4 — Register in the workspace

Open the root `pyproject.toml` and confirm the `members` glob covers your new package.
The current glob `packages/*` already does — no change needed unless you chose a
non-standard directory.

Sync the workspace to install the new package editable:

```bash
uv sync --all-packages
```

Verify the entry point is visible:

```bash
python -c "from importlib.metadata import entry_points; print(entry_points(group='resona.engines'))"
```

You should see `my-engine` listed alongside the built-in engines.

### Step 5 — Verify the engine starts

```bash
RESONA_ENGINE=my-engine uv run resona-engine-myengine
```

The server should start on `:7001` and `/health` should return your engine name:

```json
{"status": "ok", "engine": "my-engine", "models": ["my-model-v1"]}
```

### Step 6 — Test the engine

Create `packages/engine-myengine/tests/test_transcriber.py`:

```python
import numpy as np
import pytest
from unittest.mock import MagicMock, patch

from resona_asr_core.protocol import TranscriptionResult


def test_transcribe_returns_required_fields():
    """Engine must return text, language, and segments."""
    from resona_engine_myengine.transcriber import MyEngineTranscriber

    # Patch the backend so no model is actually loaded
    with patch("resona_engine_myengine.transcriber.MyBackend") as MockBackend:
        mock_instance = MagicMock()
        mock_instance.run.return_value = MagicMock(
            text="hello world",
            language="de",
            segments=[{"start": 0.0, "end": 1.0, "text": "hello world"}],
        )
        MockBackend.return_value = mock_instance

        t = MyEngineTranscriber(device="cpu")
        audio = np.zeros(16000, dtype=np.float32)
        result = t.transcribe(audio, language="de")

    assert result["text"] == "hello world"
    assert result["language"] == "de"
    assert isinstance(result["segments"], list)


def test_transcribe_no_postprocessing():
    """Engine must return raw text without applying replacements."""
    from resona_engine_myengine.transcriber import MyEngineTranscriber

    with patch("resona_engine_myengine.transcriber.MyBackend") as MockBackend:
        raw_text = "Komma bitte Punkt"
        mock_instance = MagicMock()
        mock_instance.run.return_value = MagicMock(
            text=raw_text, language="de", segments=[]
        )
        MockBackend.return_value = mock_instance

        t = MyEngineTranscriber(device="cpu")
        result = t.transcribe(np.zeros(16000, dtype=np.float32))

    # "Komma" must NOT have been replaced with "," — that is the caller's job
    assert result["text"] == raw_text
```

Also add an HTTP-level contract test using the shared engine-server fixture
pattern (mock `resona_engine_server.app.get_transcriber`):

```python
import io
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from resona_asr_core.protocol import TranscriptionResult


def test_http_contract():
    mock_t = MagicMock()
    mock_t.transcribe.return_value = TranscriptionResult(
        text="raw transcript", language="de", segments=[]
    )

    with patch("resona_engine_server.app.get_transcriber", return_value=mock_t):
        with patch("resona_engine_server.auth.config", return_value=None):
            from resona_engine_server.app import app
            client = TestClient(app)

    with patch("resona_engine_server.app.load_audio", return_value=__import__("numpy").zeros(16000)):
        resp = client.post(
            "/transcribe",
            files={"audio_file": ("t.wav", io.BytesIO(b"\x00" * 100), "audio/wav")},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"text", "language", "segments"}
    assert "md" not in body
```

Run the tests:

```bash
uv run pytest packages/engine-myengine/tests/
```

### Step 7 — Register in config.json for CLI use

To make `resona transcribe --engine my-engine` work without setting environment
variables, add a server entry to `~/.resona/config.json`:

```json
{
  "entries": [
    {
      "name": "my-engine",
      "type": "resona-api",
      "url": "http://localhost:7000"
    }
  ],
  "default_engine": "my-engine"
}
```

Then start the engine and API with:

```bash
RESONA_ENGINE=my-engine uv run resona-engine-myengine &
uv run resona-api &
uv run resona transcribe ./audio/ --engine my-engine
```

---

## Engine contract checklist

Before opening a pull request, verify every item:

- [ ] `transcribe()` signature matches the `Transcriber` protocol exactly
- [ ] `TranscriptionResult` imported from `resona_asr_core.protocol`
- [ ] Constructor is `__init__(self, device: str, modelname: str | None = None)`
- [ ] Model config read via `config()` from `python-decouple`, not `os.environ[]`
- [ ] No database imports anywhere in the engine package
- [ ] No calls to `apply_replacements()` or any postprocess module
- [ ] No file deletion anywhere in the engine package
- [ ] Entry-point key in `pyproject.toml` matches the intended `RESONA_ENGINE` value
- [ ] `[project.scripts]` points to `resona_engine_server.run:main`
- [ ] Tests cover: required fields returned, no postprocessing applied, HTTP contract
