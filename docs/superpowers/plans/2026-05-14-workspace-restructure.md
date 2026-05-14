# Workspace Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `resona-cli` installable as a standalone tool (`uv tool install`) that works in two modes — pure HTTP client or fully local with an in-process ASR backend — by splitting `resona-engine-core` into a lean shared library + a FastAPI server, and making heavy dependencies optional extras.

**Architecture:**
- Split `resona-engine-core` (1047 LOC, drags FastAPI + uvicorn + websockets) into `resona-asr-core` (protocol, registry, audio, live_transcriber — numpy + ffmpeg-python only) and `resona-engine-server` (FastAPI HTTP/WS layer).
- Slim `resona-cli` to a minimal core (typer, httpx, client) and gate `record`/`live`/local-engine functionality behind optional extras.
- Replace the subprocess-based `LocalEngine` with an `InProcessEngine` that calls `resona_asr_core.registry.get_transcriber()` directly — no subprocess, no `/health` polling.
- Each phase leaves the workspace fully green (`uv run pytest` passes) so phases can be merged independently.

**Tech Stack:** uv workspace, hatchling build backend, pytest, FastAPI, typer, faster-whisper / OpenAI Whisper / Transformers, numpy, ffmpeg-python.

---

## Target file structure

```
resona/
├── pyproject.toml                          # workspace marker
├── docker-compose.resona.yml
├── packages/
│   ├── asr-core/                           # NEW (split from engine-core)
│   │   ├── pyproject.toml                  # deps: numpy, ffmpeg-python, decouple
│   │   └── src/resona_asr_core/
│   │       ├── __init__.py
│   │       ├── protocol.py                 # moved from engine-core
│   │       ├── registry.py                 # moved
│   │       ├── audio.py                    # moved
│   │       └── live_transcriber.py         # moved
│   ├── engine-server/                      # RENAMED engine-core, FastAPI only
│   │   ├── pyproject.toml                  # deps: fastapi, uvicorn, websockets, asr-core
│   │   └── src/resona_engine_server/
│   │       ├── __init__.py
│   │       ├── app.py                      # was engine-core/app.py
│   │       ├── auth.py
│   │       ├── ws_live.py
│   │       ├── ws_transcribe.py
│   │       └── run.py
│   ├── engine-faster-whisper/              # unchanged location, deps switched to asr-core
│   ├── engine-whisper/                     # ditto
│   ├── engine-voxtral/                     # ditto
│   ├── postprocess/                        # unchanged
│   ├── api/                                # unchanged
│   └── client/                             # unchanged
├── apps/
│   ├── resona-cli/                         # core leaned; new engine.py module
│   │   └── src/resona_cli/
│   │       ├── engine.py                   # NEW: Engine protocol, RemoteEngine, InProcessEngine
│   │       └── ...
│   └── web/                                # unchanged (not a uv workspace member)
└── docs/
```

---

## Phase 1 — Make `uv tool install` work for the lean CLI

Quick win. No structural changes. Just moves heavy deps to extras and verifies the install path.

### Task 1.1: Move CLI heavy deps to optional extras

**Files:**
- Modify: `apps/resona-cli/pyproject.toml`
- Modify: `apps/resona-cli/src/resona_cli/main.py`
- Modify: `apps/resona-cli/src/resona_cli/micrec.py:1-20` (no functional change — just confirms top-level imports stay)
- Modify: `apps/resona-cli/src/resona_cli/live_ui.py:1-30`
- Test: `apps/resona-cli/tests/test_extras.py` (new)

- [ ] **Step 1: Write the failing test for missing-extra error message**

Create `apps/resona-cli/tests/test_extras.py`:

```python
"""Verify lazy-loaded TUI commands give helpful errors when their extras aren't installed."""
import sys
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from resona_cli.main import app

runner = CliRunner()


def test_rec_without_record_extra_shows_install_hint(monkeypatch):
    """Running `resona rec` without textual/sounddevice gives a clear install hint."""
    # Pretend textual isn't installed by hiding it from importlib.
    real_import = __import__

    def hide_textual(name, *args, **kwargs):
        if name == "textual" or name.startswith("textual.") or name == "sounddevice":
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    # Clear cached imports so the hide takes effect.
    for mod in list(sys.modules):
        if mod.startswith("resona_cli.micrec") or mod == "textual" or mod == "sounddevice":
            sys.modules.pop(mod, None)

    monkeypatch.setattr("builtins.__import__", hide_textual)
    result = runner.invoke(app, ["rec"])
    assert result.exit_code != 0
    assert "pip install" in result.output.lower() or "uv tool install" in result.output.lower()
    assert "[record]" in result.output or "record" in result.output.lower()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest apps/resona-cli/tests/test_extras.py -v
```

Expected: FAIL — current code raises a bare `ModuleNotFoundError` with no install hint.

- [ ] **Step 3: Add helpful import-error wrapper in `main.py`**

Replace the body of each TUI command in `apps/resona-cli/src/resona_cli/main.py` to wrap the lazy import:

```python
def _require_extra(extra: str, *modules: str) -> None:
    """Import each module name; raise typer.Exit with install hint on failure."""
    missing = []
    for m in modules:
        try:
            __import__(m)
        except ImportError:
            missing.append(m)
    if missing:
        typer.echo(
            f"Missing dependencies for this command: {', '.join(missing)}.\n"
            f"Install with:  uv tool install 'resona-cli[{extra}]'\n"
            f"or:            pip install 'resona-cli[{extra}]'",
            err=True,
        )
        raise typer.Exit(2)


@app.command()
def rec():
    """Launch the audio recorder TUI."""
    _require_extra("record", "textual", "sounddevice", "soundfile")
    from .micrec import run_mic_rec_app
    run_mic_rec_app()


@app.command()
def live():
    """Launch the live transcription TUI."""
    _require_extra("live", "textual", "sounddevice", "soundfile", "torchaudio")
    import logging
    from dotenv import load_dotenv
    import sounddevice as sd

    load_dotenv()

    logging.root.handlers.clear()
    logging.root.addHandler(logging.NullHandler())

    output_dir = os.getenv("FILE_PATH", os.path.join(os.getcwd(), "data", "files"))
    sample_rate = int(os.getenv("SAMPLE_RATE", 44100))
    channels = int(os.getenv("CHANNELS", 1))

    if not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir)
        except Exception as e:
            sys.stderr.write(f"Error: Could not create output directory {output_dir}: {e}\n")
            raise typer.Exit(1)

    try:
        sd.check_input_settings(device=None, samplerate=sample_rate, channels=channels)
    except Exception as e:
        sys.stderr.write(f"Error initializing audio input: {e}\n")
        raise typer.Exit(1)

    from .live_ui import WSLiveApp
    WSLiveApp().run()


@app.command()
def ui():
    """Launch the record-and-transcribe TUI (records, submits job, shows result)."""
    _require_extra("record", "textual", "sounddevice", "soundfile")
    import logging
    from dotenv import load_dotenv
    import sounddevice as sd

    load_dotenv()

    logging.root.handlers.clear()
    logging.root.addHandler(logging.NullHandler())

    output_dir = os.getenv("FILE_PATH", os.path.join(os.getcwd(), "data", "files"))
    sample_rate = int(os.getenv("SAMPLE_RATE", 44100))
    channels = int(os.getenv("CHANNELS", 1))

    os.makedirs(output_dir, exist_ok=True)

    try:
        sd.check_input_settings(device=None, samplerate=sample_rate, channels=channels)
    except Exception as e:
        sys.stderr.write(f"Error initializing audio input: {e}\n")
        raise typer.Exit(1)

    from .ui import WSUIApp
    WSUIApp().run()
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest apps/resona-cli/tests/test_extras.py -v
```

Expected: PASS.

- [ ] **Step 5: Update `apps/resona-cli/pyproject.toml` to move heavy deps to extras**

Replace the entire `[project]` and add `[project.optional-dependencies]`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "resona-cli"
version = "0.1.0"
description = "CLI tool for Resona transcription platform"
requires-python = ">=3.12"
dependencies = [
    "httpx>=0.28.1",
    "typer>=0.15.3",
    "python-dotenv>=1.1.0",
    "python-decouple>=3.8",
    "resona-client",
    "resona-postprocess",
]

[project.optional-dependencies]
record = [
    "textual>=3.2.0",
    "sounddevice>=0.5.1",
    "soundfile>=0.13.1",
    "numpy>=2.1.3",
]
live = [
    "textual>=3.2.0",
    "sounddevice>=0.5.1",
    "soundfile>=0.13.1",
    "numpy>=2.1.3",
    "torchaudio>=2.11.0.dev",
    "resona-engine-core",
]

[tool.uv.sources]
resona-client = { workspace = true }
resona-postprocess = { workspace = true }
resona-engine-core = { workspace = true }

[project.scripts]
resona = "resona_cli.main:app"

[tool.hatch.build.targets.wheel]
packages = ["src/resona_cli"]
```

Note: `resona-engine-core` is now only required by `[live]`. After Phase 2 it will become `resona-asr-core`.

- [ ] **Step 6: Re-sync the workspace and run the full suite**

```bash
uv sync --all-packages --no-build-isolation-package openai-whisper
uv run pytest apps/resona-cli/tests/ -v
```

Expected: PASS for all transcribe/extras/watch tests. (Pre-existing 3 backend test failures unrelated to this change can be ignored.)

- [ ] **Step 7: Verify `uv tool install` succeeds for the lean CLI**

```bash
uv tool install --force --from ./apps/resona-cli resona-cli
resona --help
resona transcribe --help
```

Expected: `--help` works. `resona rec` should print the install hint.

- [ ] **Step 8: Commit**

```bash
git add apps/resona-cli/pyproject.toml apps/resona-cli/src/resona_cli/main.py apps/resona-cli/tests/test_extras.py
git commit -m "$(cat <<'EOF'
refactor(cli): move record/live deps to optional extras

resona-cli now installs as a lean HTTP client by default. textual,
sounddevice, soundfile, torchaudio, and resona-engine-core are gated
behind `[record]` and `[live]` extras. Lazy-loaded commands print a
helpful install hint when the relevant extra is missing.

uv tool install --from ./apps/resona-cli resona-cli now works.
EOF
)"
```

---

## Phase 2 — Split `resona-engine-core` into `resona-asr-core` + `resona-engine-server`

Lift everything that doesn't need FastAPI out of `engine-core` into a lean `asr-core` package, and rename what remains.

### Task 2.1: Create the new `resona-asr-core` package skeleton

**Files:**
- Create: `packages/asr-core/pyproject.toml`
- Create: `packages/asr-core/src/resona_asr_core/__init__.py`
- Create: `packages/asr-core/tests/__init__.py`
- Modify: `pyproject.toml` (workspace root) — add new member

- [ ] **Step 1: Create the package directory and pyproject**

`packages/asr-core/pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "resona-asr-core"
version = "0.1.0"
description = "Lean ASR contracts for Resona — protocol, registry, audio loader, live transcriber. No FastAPI."
requires-python = ">=3.12"
dependencies = [
    "numpy>=2.1.3",
    "ffmpeg-python>=0.2.0",
    "python-decouple>=3.8",
]

[tool.hatch.build.targets.wheel]
packages = ["src/resona_asr_core"]
```

Create empty `packages/asr-core/src/resona_asr_core/__init__.py` and `packages/asr-core/tests/__init__.py`.

- [ ] **Step 2: Register the new package in the workspace**

Modify the workspace root `pyproject.toml`:

```toml
[tool.uv.workspace]
members = ["packages/*", "apps/resona-cli"]
```

That glob already matches `packages/asr-core`, so no change needed if it already uses `packages/*`. Verify by running:

```bash
uv sync --all-packages --no-build-isolation-package openai-whisper 2>&1 | grep -E "asr-core|engine-core"
```

Expected: `resona-asr-core` appears in the output.

- [ ] **Step 3: Commit the skeleton**

```bash
git add packages/asr-core/
git commit -m "feat(asr-core): add empty package skeleton"
```

### Task 2.2: Move `protocol.py`, `registry.py`, `audio.py` from engine-core to asr-core

**Files:**
- Move: `packages/engine-core/src/resona_engine_core/protocol.py` → `packages/asr-core/src/resona_asr_core/protocol.py`
- Move: `packages/engine-core/src/resona_engine_core/registry.py` → `packages/asr-core/src/resona_asr_core/registry.py`
- Move: `packages/engine-core/src/resona_engine_core/audio.py` → `packages/asr-core/src/resona_asr_core/audio.py`
- Modify: `packages/engine-core/src/resona_engine_core/__init__.py` (add re-exports for back-compat)
- Modify: `packages/engine-core/pyproject.toml` (add `resona-asr-core` dep)

- [ ] **Step 1: `git mv` the three files**

```bash
git mv packages/engine-core/src/resona_engine_core/protocol.py \
       packages/asr-core/src/resona_asr_core/protocol.py
git mv packages/engine-core/src/resona_engine_core/registry.py \
       packages/asr-core/src/resona_asr_core/registry.py
git mv packages/engine-core/src/resona_engine_core/audio.py \
       packages/asr-core/src/resona_asr_core/audio.py
```

- [ ] **Step 2: Update engine-core's deps to pull in asr-core**

Modify `packages/engine-core/pyproject.toml`:

```toml
[project]
name = "resona-engine-core"
version = "0.1.0"
description = "Core FastAPI app, transcriber protocol, and audio utilities for Resona"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.7",
    "uvicorn>=0.34.0",
    "python-multipart>=0.0.20",
    "websockets>=16.0",
    "python-decouple>=3.8",
    "resona-asr-core",
]

[tool.uv.sources]
resona-asr-core = { workspace = true }

[project.scripts]
resona-engine-core = "resona_engine_core.run:main"

[tool.hatch.build.targets.wheel]
packages = ["src/resona_engine_core"]
```

- [ ] **Step 3: Add back-compat re-exports in `engine-core/__init__.py`**

`packages/engine-core/src/resona_engine_core/__init__.py`:

```python
"""resona-engine-core — kept for backwards compatibility.

The lean contracts (protocol, registry, audio, live_transcriber) live in
`resona-asr-core`. This package now contains only the FastAPI HTTP/WS server.
Re-exports below let existing imports `from resona_engine_core import X` keep
working during the migration.
"""
from resona_asr_core.protocol import Transcriber, TranscriptionResult  # noqa: F401
from resona_asr_core.registry import get_transcriber, reset  # noqa: F401
from resona_asr_core.audio import load_audio, SAMPLE_RATE  # noqa: F401
```

- [ ] **Step 4: Fix internal imports inside engine-core**

In `packages/engine-core/src/resona_engine_core/app.py`, `ws_transcribe.py`, `ws_live.py`, `run.py`, replace `from .protocol`, `from .registry`, `from .audio` with `from resona_asr_core.protocol`, etc.

Run a quick grep + sed:

```bash
grep -rln "from \.protocol\|from \.registry\|from \.audio" packages/engine-core/src/
```

For each match, change `from .protocol` → `from resona_asr_core.protocol`, `from .registry` → `from resona_asr_core.registry`, `from .audio` → `from resona_asr_core.audio`.

- [ ] **Step 5: Run engine-core tests to confirm green**

```bash
uv run pytest packages/engine-core/tests/ -v
```

Expected: PASS (or same failures as before the split — no new failures).

- [ ] **Step 6: Run the full suite**

```bash
uv run pytest 2>&1 | tail -5
```

Expected: same pass/fail count as before this task.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor(asr-core): extract protocol/registry/audio from engine-core

Moves the lean ASR contracts (Transcriber protocol, entry-point
registry, ffmpeg audio loader) into a new resona-asr-core package
with no FastAPI dependency. engine-core re-exports them for back-compat.
EOF
)"
```

### Task 2.3: Move `live_transcriber.py` into asr-core

**Files:**
- Move: `packages/engine-core/src/resona_engine_core/live_transcriber.py` → `packages/asr-core/src/resona_asr_core/live_transcriber.py`
- Modify: `packages/asr-core/src/resona_asr_core/live_transcriber.py` (one import fix)
- Modify: `packages/engine-core/src/resona_engine_core/__init__.py` (add re-export)
- Modify: `packages/engine-core/src/resona_engine_core/ws_live.py` (update import path)

- [ ] **Step 1: Move the file**

```bash
git mv packages/engine-core/src/resona_engine_core/live_transcriber.py \
       packages/asr-core/src/resona_asr_core/live_transcriber.py
```

- [ ] **Step 2: Verify relative imports still resolve**

`live_transcriber.py` line 24 has `from .registry import get_transcriber`. Since both files are now in `resona_asr_core`, this stays as-is.

- [ ] **Step 3: Add back-compat re-export**

Append to `packages/engine-core/src/resona_engine_core/__init__.py`:

```python
from resona_asr_core.live_transcriber import LiveTranscriber  # noqa: F401
```

- [ ] **Step 4: Fix `ws_live.py`'s internal import**

In `packages/engine-core/src/resona_engine_core/ws_live.py`, replace `from .live_transcriber import LiveTranscriber` with `from resona_asr_core.live_transcriber import LiveTranscriber`.

- [ ] **Step 5: Run engine-core + asr-core tests**

```bash
uv run pytest packages/engine-core/tests/ packages/asr-core/tests/ -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(asr-core): move live_transcriber from engine-core"
```

### Task 2.4: Switch backends to depend on `resona-asr-core`

**Files:**
- Modify: `packages/engine-faster-whisper/pyproject.toml`
- Modify: `packages/engine-whisper/pyproject.toml`
- Modify: `packages/engine-voxtral/pyproject.toml`
- Modify: `packages/engine-faster-whisper/src/resona_engine_faster_whisper/transcriber.py` (and the other two)

- [ ] **Step 1: Update faster-whisper backend pyproject**

`packages/engine-faster-whisper/pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "resona-engine-faster-whisper"
version = "0.1.0"
description = "faster-whisper backend for Resona"
requires-python = ">=3.12"
dependencies = [
    "resona-asr-core",
    "faster-whisper>=1.1.1",
    "torch>=2.11.0.dev",
]

[tool.uv.sources]
resona-asr-core = { workspace = true }

[project.entry-points."resona.backends"]
faster-whisper = "resona_engine_faster_whisper.transcriber:FastWhisperTranscriber"

[project.scripts]
resona-engine-faster-whisper = "resona_engine_core.run:main"

[tool.hatch.build.targets.wheel]
packages = ["src/resona_engine_faster_whisper"]
```

Note: keep the `[project.scripts]` pointing at `resona_engine_core.run:main` until we rename engine-core → engine-server in Task 2.5.

- [ ] **Step 2: Update the backend's transcriber import**

Inside `packages/engine-faster-whisper/src/resona_engine_faster_whisper/transcriber.py`, find any `from resona_engine_core` import and change it to `from resona_asr_core`:

```bash
grep -n "resona_engine_core" packages/engine-faster-whisper/src/resona_engine_faster_whisper/transcriber.py
```

Replace each match (likely just `from resona_engine_core.protocol import ...`).

- [ ] **Step 3: Repeat steps 1-2 for engine-whisper**

`packages/engine-whisper/pyproject.toml` — change `resona-engine-core` → `resona-asr-core` in `dependencies` and `[tool.uv.sources]`. Update the transcriber import.

- [ ] **Step 4: Repeat steps 1-2 for engine-voxtral**

Same pattern.

- [ ] **Step 5: Run all backend tests**

```bash
uv run pytest packages/engine-faster-whisper/tests/ packages/engine-whisper/tests/ packages/engine-voxtral/tests/ -v
```

Expected: PASS (these tests usually mock the heavy ML libs).

- [ ] **Step 6: Run the full suite**

```bash
uv run pytest 2>&1 | tail -5
```

Expected: same green/red count as before.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(backends): depend on resona-asr-core instead of engine-core"
```

### Task 2.5: Rename `resona-engine-core` → `resona-engine-server`

This separates the role (FastAPI HTTP/WS server) from the contracts (asr-core).

**Files:**
- Move: `packages/engine-core/` → `packages/engine-server/`
- Move: `packages/engine-server/src/resona_engine_core/` → `packages/engine-server/src/resona_engine_server/`
- Modify: every file under `packages/engine-server/src/resona_engine_server/` to update imports
- Modify: every `pyproject.toml` referencing `resona-engine-core` or `resona_engine_core` (backends' `[project.scripts]`, CLI's `[live]` extra)

- [ ] **Step 1: Rename directories**

```bash
git mv packages/engine-core packages/engine-server
git mv packages/engine-server/src/resona_engine_core packages/engine-server/src/resona_engine_server
```

- [ ] **Step 2: Rewrite the server pyproject**

`packages/engine-server/pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "resona-engine-server"
version = "0.1.0"
description = "FastAPI HTTP/WS server that exposes a Resona ASR backend"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.7",
    "uvicorn>=0.34.0",
    "python-multipart>=0.0.20",
    "websockets>=16.0",
    "python-decouple>=3.8",
    "resona-asr-core",
]

[tool.uv.sources]
resona-asr-core = { workspace = true }

[project.scripts]
resona-engine-server = "resona_engine_server.run:main"

[tool.hatch.build.targets.wheel]
packages = ["src/resona_engine_server"]
```

- [ ] **Step 3: Strip back-compat re-exports from the new `__init__.py`**

Now that everything depends on `resona-asr-core` directly, we don't need the re-exports.

`packages/engine-server/src/resona_engine_server/__init__.py`:

```python
"""resona-engine-server — FastAPI HTTP/WS server exposing a Resona ASR backend."""
```

- [ ] **Step 4: Update each backend's `[project.scripts]`**

In `packages/engine-faster-whisper/pyproject.toml`, `packages/engine-whisper/pyproject.toml`, `packages/engine-voxtral/pyproject.toml`:

```toml
[project.scripts]
resona-engine-faster-whisper = "resona_engine_server.run:main"
```

(Adjust the script name per backend.)

- [ ] **Step 5: Update API's engine URL default and CLI's `[live]` extra**

`packages/api/src/resona_api/engine_client.py` — leave the URL config alone (it's `RESONA_ENGINE_URL`, behaviour unchanged).

`apps/resona-cli/pyproject.toml` `[live]` extra:

```toml
live = [
    "textual>=3.2.0",
    "sounddevice>=0.5.1",
    "soundfile>=0.13.1",
    "numpy>=2.1.3",
    "torchaudio>=2.11.0.dev",
    "resona-asr-core",
]
```

(`resona-engine-core` is gone — live uses `resona-asr-core` directly for `LiveTranscriber`.)

`apps/resona-cli/src/resona_cli/live_ui.py` line 21:

```python
from resona_asr_core.live_transcriber import LiveTranscriber
from resona_asr_core.audio import SAMPLE_RATE as ASR_SAMPLE_RATE
```

- [ ] **Step 6: Update `local_engine.py` package name reference**

`apps/resona-cli/src/resona_cli/local_engine.py:42` currently builds the subprocess command via `self._package = f"resona-engine-{backend}"`. This still points to the backend's script (e.g. `resona-engine-faster-whisper`), which after the rename still exists. No change needed — only the script's *body* moved (now resolves `resona_engine_server.run:main`).

- [ ] **Step 7: Update Dockerfile references**

```bash
grep -rln "resona-engine-core\|resona_engine_core" packages/ apps/ docker-compose.resona.yml
```

For each Dockerfile (likely `packages/engine-faster-whisper/Dockerfile`, etc.), update `COPY packages/engine-core/` → `COPY packages/engine-server/` and `RUN uv sync --package resona-engine-core` → `RUN uv sync --package resona-engine-server`.

- [ ] **Step 8: Run the full test suite**

```bash
uv sync --all-packages --no-build-isolation-package openai-whisper
uv run pytest 2>&1 | tail -5
```

Expected: same green/red count as before.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor: rename resona-engine-core → resona-engine-server

engine-core was doing two jobs: ASR contracts (now in resona-asr-core)
and an HTTP/WS server. Rename clarifies the remaining role.
Backend Docker images, scripts, and entry points updated.
EOF
)"
```

---

## Phase 3 — In-process local engine

Replace the subprocess-based `LocalEngine` with an `InProcessEngine` that calls `resona_asr_core.registry.get_transcriber()` directly. Keep the old `LocalEngine` as a fallback for users who don't install a backend extra.

### Task 3.1: Define the `Engine` protocol and `RemoteEngine` adapter

**Files:**
- Create: `apps/resona-cli/src/resona_cli/engine.py`
- Test: `apps/resona-cli/tests/test_engine.py`

- [ ] **Step 1: Write the failing test for the Engine contract**

`apps/resona-cli/tests/test_engine.py`:

```python
"""Tests for the Engine abstraction used by `resona transcribe` local mode."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from resona_cli.engine import Engine, RemoteEngine


def test_remote_engine_satisfies_protocol():
    """RemoteEngine implements the Engine Protocol."""
    e = RemoteEngine(api_url="http://x:7000")
    assert isinstance(e, Engine)


def test_remote_engine_delegates_to_resona_client(tmp_path):
    """RemoteEngine.transcribe submits a job and waits for the result via ResonaClient."""
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"fake")

    mock_client = MagicMock()
    mock_client.submit_job.return_value = {"id": 7}
    mock_client.wait_for_job.return_value = {
        "status": "completed", "transcript": "hi", "md": "", "language": "de",
    }

    with patch("resona_cli.engine.ResonaClient", return_value=mock_client):
        engine = RemoteEngine(api_url="http://x:7000")
        result = engine.transcribe(audio, language="de")

    assert result["text"] == "hi"
    assert result["language"] == "de"
    mock_client.submit_job.assert_called_once_with(audio)
    mock_client.wait_for_job.assert_called_once_with(7)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest apps/resona-cli/tests/test_engine.py -v
```

Expected: FAIL — `resona_cli.engine` doesn't exist.

- [ ] **Step 3: Implement `engine.py`**

`apps/resona-cli/src/resona_cli/engine.py`:

```python
"""Engine abstraction — uniform interface for remote (HTTP) and in-process transcription."""
from pathlib import Path
from typing import Protocol, TypedDict

from resona_client.client import ResonaClient


class TranscriptionResult(TypedDict):
    text: str
    language: str
    segments: list


class Engine(Protocol):
    def transcribe(self, audio: Path, **kwargs) -> TranscriptionResult: ...


class RemoteEngine:
    """Submits jobs to a resona-api server and waits for the result."""

    def __init__(self, api_url: str | None = None, api_key: str | None = None) -> None:
        self._client = ResonaClient(api_url=api_url, api_key=api_key) if api_url else ResonaClient.from_config()

    def transcribe(self, audio: Path, **kwargs) -> TranscriptionResult:
        job = self._client.submit_job(audio)
        result = self._client.wait_for_job(job["id"])
        return {
            "text": result.get("md") or result.get("transcript", ""),
            "language": result.get("language", ""),
            "segments": result.get("segments", []),
        }
```

Note: `ResonaClient.__init__` may not take `api_url` / `api_key` kwargs — check the current signature in `packages/client/src/resona_client/client.py`. If it doesn't, use only the no-arg form and rely on env vars. If you need explicit url+key support, add it to `ResonaClient` in a separate task and wire it here.

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest apps/resona-cli/tests/test_engine.py -v
```

Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add apps/resona-cli/src/resona_cli/engine.py apps/resona-cli/tests/test_engine.py
git commit -m "feat(cli): add Engine protocol and RemoteEngine adapter"
```

### Task 3.2: Add `InProcessEngine` using the asr-core registry

**Files:**
- Modify: `apps/resona-cli/src/resona_cli/engine.py` (add class)
- Modify: `apps/resona-cli/tests/test_engine.py` (add tests)

- [ ] **Step 1: Write the failing test**

Append to `apps/resona-cli/tests/test_engine.py`:

```python
from unittest.mock import MagicMock, patch
import numpy as np


def test_in_process_engine_calls_registry(tmp_path):
    """InProcessEngine.transcribe loads a backend via get_transcriber and calls it."""
    from resona_cli.engine import InProcessEngine

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"fake")

    mock_transcriber = MagicMock()
    mock_transcriber.transcribe.return_value = {
        "text": "hello", "language": "de", "segments": [],
    }

    with (
        patch("resona_cli.engine._load_audio", return_value=np.zeros(16000, dtype=np.float32)),
        patch("resona_cli.engine.get_transcriber", return_value=mock_transcriber),
    ):
        engine = InProcessEngine(backend="faster-whisper")
        result = engine.transcribe(audio, language="de")

    assert result["text"] == "hello"
    mock_transcriber.transcribe.assert_called_once()


def test_in_process_engine_missing_extra_gives_install_hint():
    """If resona-asr-core isn't installed, InProcessEngine raises ImportError with hint."""
    from resona_cli.engine import InProcessEngine

    with patch("resona_cli.engine._import_asr_core", side_effect=ImportError("no asr-core")):
        with pytest.raises(ImportError, match="resona-cli\\["):
            InProcessEngine(backend="faster-whisper")
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest apps/resona-cli/tests/test_engine.py::test_in_process_engine_calls_registry -v
```

Expected: FAIL — `InProcessEngine` doesn't exist.

- [ ] **Step 3: Add `InProcessEngine` to `engine.py`**

Append to `apps/resona-cli/src/resona_cli/engine.py`:

```python
def _import_asr_core():
    """Import asr-core's registry + audio. Raises ImportError with install hint."""
    try:
        from resona_asr_core.registry import get_transcriber
        from resona_asr_core.audio import load_audio
    except ImportError as e:
        raise ImportError(
            f"{e}\n\n"
            "In-process transcription requires a backend extra. Install one:\n"
            "  uv tool install 'resona-cli[faster-whisper]'\n"
            "  uv tool install 'resona-cli[whisper]'\n"
            "  uv tool install 'resona-cli[voxtral]'"
        ) from e
    return get_transcriber, load_audio


# Re-exposed for patching in tests.
def get_transcriber(*args, **kwargs):
    fn, _ = _import_asr_core()
    return fn(*args, **kwargs)


def _load_audio(path: Path):
    _, fn = _import_asr_core()
    return fn(str(path))


class InProcessEngine:
    """Loads an ASR backend in-process via the resona-asr-core entry-point registry."""

    def __init__(self, backend: str = "faster-whisper") -> None:
        _import_asr_core()  # fail fast with hint if extra missing
        self._backend = backend
        self._transcriber = get_transcriber(backend)

    def transcribe(self, audio: Path, **kwargs) -> TranscriptionResult:
        samples = _load_audio(audio)
        return self._transcriber.transcribe(samples, **kwargs)
```

- [ ] **Step 4: Run both engine tests**

```bash
uv run pytest apps/resona-cli/tests/test_engine.py -v
```

Expected: PASS (all 4 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/resona-cli/src/resona_cli/engine.py apps/resona-cli/tests/test_engine.py
git commit -m "feat(cli): add InProcessEngine using asr-core registry"
```

### Task 3.3: Wire `transcribe` command to use `Engine` abstraction

**Files:**
- Modify: `apps/resona-cli/src/resona_cli/transcribe.py`
- Modify: `apps/resona-cli/tests/test_transcribe.py`

- [ ] **Step 1: Write a test for in-process local mode**

Append to `apps/resona-cli/tests/test_transcribe.py`:

```python
def test_transcribe_uses_in_process_engine_when_extra_installed(tmp_path):
    """When asr-core + a backend is installed and no server is reachable, use InProcessEngine."""
    make_wav(tmp_path / "audio.wav")
    out_dir = tmp_path / "out"

    mock_engine = MagicMock()
    mock_engine.transcribe.return_value = {"text": "hi", "language": "de", "segments": []}

    with (
        patch("resona_client.client.ResonaClient.from_config", side_effect=RuntimeError("no server")),
        patch("resona_cli.transcribe.InProcessEngine", return_value=mock_engine),
        patch("resona_postprocess.sources.build_pipeline_from_config", return_value=_noop_pipeline()),
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path), "--output-dir", str(out_dir)])

    assert result.exit_code == 0
    mock_engine.transcribe.assert_called_once()
    assert (out_dir / "audio.txt").read_text() == "hi"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest apps/resona-cli/tests/test_transcribe.py::test_transcribe_uses_in_process_engine_when_extra_installed -v
```

Expected: FAIL — `resona_cli.transcribe.InProcessEngine` not importable.

- [ ] **Step 3: Update `transcribe.py` to prefer in-process when possible**

In `apps/resona-cli/src/resona_cli/transcribe.py`, replace `_transcribe_local_fallback`:

```python
def _transcribe_local_fallback(
    files: list[Path],
    output_dir: Optional[Path],
    model: Optional[str],
    language: str,
    engine_timeout: float,
    backend: str = "faster-whisper",
) -> None:
    from resona_postprocess.sources import build_pipeline_from_config

    if not files:
        print("No audio files found.")
        return

    try:
        from .engine import InProcessEngine
        engine_ctx = InProcessEngine(backend=backend)
        typer.echo(
            f"No server reachable — running backend '{backend}' in-process.",
            err=True,
        )
        engine = engine_ctx
        cleanup = lambda: None  # in-process needs no cleanup
    except ImportError:
        typer.echo(
            f"No server reachable — starting local engine subprocess (backend={backend}).",
            err=True,
        )
        engine = LocalEngine(model=model, timeout=engine_timeout, backend=backend).__enter__()
        cleanup = lambda: engine.__exit__(None, None, None)

    pipeline = build_pipeline_from_config()

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    try:
        for filepath in files:
            try:
                result = engine.transcribe(filepath, language=language)
                raw_text = result.get("text", "")
                transcript = pipeline.run(raw_text)
                out_path = (output_dir or filepath.parent) / f"{filepath.stem}.txt"
                out_path.write_text(transcript, encoding="utf-8")
                print(f"Transcribed {filepath.name} -> {out_path}")
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                typer.echo(f"Failed to transcribe {filepath.name}: {e}", err=True)
    finally:
        cleanup()
```

Add the import near the top:

```python
from .engine import InProcessEngine  # noqa: F401 — imported here so tests can patch the symbol
```

- [ ] **Step 4: Run the new test plus the existing fallback tests**

```bash
uv run pytest apps/resona-cli/tests/test_transcribe.py -v
```

Expected: all transcribe tests PASS, including the new one and the existing subprocess-fallback tests (the `LocalEngine` path is still exercised when `InProcessEngine` import fails, which the existing tests mock at the `resona_cli.transcribe.LocalEngine` patch site).

- [ ] **Step 5: Commit**

```bash
git add apps/resona-cli/src/resona_cli/transcribe.py apps/resona-cli/tests/test_transcribe.py
git commit -m "feat(cli): prefer in-process backend over subprocess in local fallback"
```

---

## Phase 4 — Backend extras for `resona-cli`

Make the backend packages installable as extras of the CLI, so `uv tool install 'resona-cli[faster-whisper]'` gives a fully-local install.

### Task 4.1: Add backend extras to CLI pyproject

**Files:**
- Modify: `apps/resona-cli/pyproject.toml`
- Test: `apps/resona-cli/tests/test_extras.py` (extend)

- [ ] **Step 1: Extend the extras tests**

Append to `apps/resona-cli/tests/test_extras.py`:

```python
def test_in_process_engine_without_backend_extra_shows_hint(monkeypatch):
    """Constructing InProcessEngine without resona-asr-core gives an install hint."""
    from resona_cli.engine import InProcessEngine

    real_import = __import__

    def hide_asr_core(name, *args, **kwargs):
        if name == "resona_asr_core" or name.startswith("resona_asr_core."):
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", hide_asr_core)

    with pytest.raises(ImportError, match=r"resona-cli\["):
        InProcessEngine(backend="faster-whisper")
```

- [ ] **Step 2: Run the test**

```bash
uv run pytest apps/resona-cli/tests/test_extras.py::test_in_process_engine_without_backend_extra_shows_hint -v
```

Expected: PASS (the hint message in `_import_asr_core` was already added in Task 3.2).

- [ ] **Step 3: Add backend extras to CLI pyproject**

`apps/resona-cli/pyproject.toml`:

```toml
[project.optional-dependencies]
record = [
    "textual>=3.2.0",
    "sounddevice>=0.5.1",
    "soundfile>=0.13.1",
    "numpy>=2.1.3",
]
live = [
    "textual>=3.2.0",
    "sounddevice>=0.5.1",
    "soundfile>=0.13.1",
    "numpy>=2.1.3",
    "torchaudio>=2.11.0.dev",
    "resona-asr-core",
]
faster-whisper = [
    "resona-asr-core",
    "resona-engine-faster-whisper",
]
whisper = [
    "resona-asr-core",
    "resona-engine-whisper",
]
voxtral = [
    "resona-asr-core",
    "resona-engine-voxtral",
]

[tool.uv.sources]
resona-client = { workspace = true }
resona-postprocess = { workspace = true }
resona-asr-core = { workspace = true }
resona-engine-faster-whisper = { workspace = true }
resona-engine-whisper = { workspace = true }
resona-engine-voxtral = { workspace = true }
```

- [ ] **Step 4: Re-sync and run tests**

```bash
uv sync --all-packages --no-build-isolation-package openai-whisper
uv run pytest apps/resona-cli/tests/ -v
```

Expected: PASS.

- [ ] **Step 5: Smoke-test `uv tool install` with a backend extra**

```bash
uv tool install --force --from ./apps/resona-cli 'resona-cli[faster-whisper]' 2>&1 | tail -10
```

Expected: either succeeds, or fails with a torch-resolution error (because the nightly index isn't inherited). If it fails, document the workaround in README:

> The `[faster-whisper]` extra pulls a torch nightly. Until uv supports inheriting workspace indexes for tool installs, use `uv pip install --extra-index-url https://download.pytorch.org/whl/nightly/cu128 'resona-cli[faster-whisper]'` or stay inside the workspace with `uv run resona`.

- [ ] **Step 6: Commit**

```bash
git add apps/resona-cli/pyproject.toml apps/resona-cli/tests/test_extras.py
git commit -m "feat(cli): add [faster-whisper] / [whisper] / [voxtral] extras"
```

---

## Phase 5 — Documentation and cleanup

### Task 5.1: Update CLAUDE.md, README.md, docs/

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`
- Modify: `docs/cli.md`
- Modify: `docs/getting-started.md`
- Modify: `docs/onboarding.md`
- Modify: `docs/index.md`
- Modify: `justfile`

- [ ] **Step 1: Update CLAUDE.md package list**

In `CLAUDE.md` under "Project structure", change:

```
    ├── engine-core/        ← resona-engine-core: FastAPI app, Transcriber protocol, registry, :7001
```

to:

```
    ├── asr-core/           ← resona-asr-core: protocol, registry, audio, live transcriber (lean)
    ├── engine-server/      ← resona-engine-server: FastAPI HTTP/WS app, :7001
```

Update the "Package responsibilities" section similarly: split the old `resona-engine-core` block into `resona-asr-core` (lean library) and `resona-engine-server` (FastAPI).

Add a new "Install personas" subsection under "Running in development":

```markdown
### Install personas

| Persona | Command |
|---------|---------|
| HTTP client only | `uv tool install --from ./apps/resona-cli resona-cli` |
| Record + submit to server | `uv tool install --from ./apps/resona-cli 'resona-cli[record]'` |
| Local-only (faster-whisper) | `uv tool install --from ./apps/resona-cli 'resona-cli[faster-whisper]'` |
| Live TUI | `uv tool install --from ./apps/resona-cli 'resona-cli[live,faster-whisper]'` |
| Server operator | `uv tool install --from ./packages/engine-faster-whisper resona-engine-faster-whisper` |
```

- [ ] **Step 2: Update README.md install/usage sections**

Replace the "Local-only mode (no server)" block (around line 92) with:

```markdown
### Local-only mode (no server)

If no server is reachable, the CLI uses an in-process backend (if installed) or
spawns a subprocess as fallback. To install the CLI globally with a local
backend baked in:

```bash
uv tool install --from ./apps/resona-cli 'resona-cli[faster-whisper]'

# Now `resona transcribe file.mp3` works anywhere, no server needed.
resona transcribe file.mp3
resona transcribe "recordings/*.mp3"
resona transcribe ./recordings/ --output-dir ./out/
```

For HTTP-only usage (talk to an existing server), drop the extra:

```bash
uv tool install --from ./apps/resona-cli resona-cli
resona backends add lan http://192.168.1.10:7000
resona transcribe file.mp3
```
```

- [ ] **Step 3: Update docs/cli.md, docs/getting-started.md, docs/onboarding.md, docs/index.md**

For each file:
- Replace `resona-engine-core` references in architecture diagrams with `resona-asr-core` + `resona-engine-server`.
- Update install instructions to use the new extras.

- [ ] **Step 4: Fix the stale justfile**

`justfile` currently references `ws-engine`, `ws-api`, `ws-cli` — leftover from a pre-resona rename. Replace:

```makefile
# Start the transcription engine on :7001 (GPU required)
engine:
    uv run resona-engine-faster-whisper

# Start the job queue API on :7000
api:
    uv run resona-api

# Run all tests (pass extra args: just test -k test_transcribe)
test *args:
    uv run pytest {{ args }}

test-api:
    uv run pytest packages/api/tests/

test-engine:
    uv run pytest packages/engine-server/tests/ packages/asr-core/tests/

test-client:
    uv run pytest packages/client/tests/

test-cli:
    uv run pytest apps/resona-cli/tests/

rec:
    uv run resona rec

live:
    uv run resona live

ui:
    uv run resona ui

# Transcribe files, globs, or directories
transcribe args:
    uv run resona transcribe {{ args }}

watch dir:
    uv run resona watch {{ dir }}

backends:
    uv run resona backends list
```

(Remove the obsolete `batch dir:` recipe.)

- [ ] **Step 5: Run the full test suite + doc smoke-build**

```bash
uv run pytest 2>&1 | tail -5
uv run mkdocs build 2>&1 | tail -5
```

Expected: tests green, mkdocs builds without "missing file" errors.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md README.md docs/ justfile
git commit -m "docs: document split asr-core/engine-server packages and install personas"
```

### Task 5.2: Remove engine-core back-compat shim

By now, `resona-engine-core` is renamed to `resona-engine-server` and no package depends on `resona_engine_core` anymore. The interim re-exports in `__init__.py` (added in Tasks 2.2 / 2.3) were already deleted when we renamed in Task 2.5 — verify nothing's left.

- [ ] **Step 1: Grep for leftover references**

```bash
grep -rn "resona_engine_core\|resona-engine-core" packages/ apps/ docs/ docker-compose.resona.yml 2>&1 | grep -v "\.venv\|site-packages\|docs/superpowers/specs\|docs/superpowers/plans"
```

Expected: no output (or only historical mentions in plan/spec docs, which is fine).

- [ ] **Step 2: If any code references remain, fix them**

For each non-doc reference, replace `resona_engine_core` → `resona_engine_server` for FastAPI/server code, `resona_asr_core` for protocol/registry/audio code. Commit.

- [ ] **Step 3: Verify the final architecture compiles**

```bash
uv sync --all-packages --no-build-isolation-package openai-whisper
uv run pytest 2>&1 | tail -5
```

Expected: clean install + tests green.

- [ ] **Step 4: Final commit (if any changes)**

```bash
git add -A
git commit -m "chore: drop final resona-engine-core back-compat references"
```

---

## Verification matrix (run after the whole plan completes)

| Check | Command | Expected |
|---|---|---|
| Lean install works | `uv tool install --force --from ./apps/resona-cli resona-cli && resona --help` | Help text printed |
| Lean install gives hint for missing extras | `resona rec` (after lean install) | "Install with: uv tool install 'resona-cli[record]'" |
| Local install works | `uv tool install --force --from ./apps/resona-cli 'resona-cli[faster-whisper]' && resona transcribe testfile.mp3` | Transcript written (or torch index hint if blocked) |
| Workspace tests green | `uv run pytest 2>&1 \| tail -5` | Same pass/fail count as start of plan, minus the 3 pre-existing backend test failures |
| Server still boots | `uv run resona-engine-faster-whisper` | `:7001/health` returns OK |
| API still boots | `uv run resona-api` | `:7000/health` returns OK |
| Docs build | `uv run mkdocs build` | No missing-file errors |

---

## Notes on risk and rollback

- **Phase 1** is independent and reversible. If something goes wrong, revert the single commit.
- **Phase 2** keeps re-exports in `resona_engine_core.__init__` until Task 2.5, so the workspace stays green throughout. The point of no return is Task 2.5 (the directory rename); after that, the old name doesn't resolve.
- **Phase 3** is additive (new `engine.py`, no removals). The old `LocalEngine` subprocess path stays as fallback.
- **Phase 4** adds extras only — no breaking changes.
- **Phase 5** is documentation, no risk.

A reasonable shipping cadence is one PR per phase (5 PRs total). Phase 1 alone is shippable today and unblocks `uv tool install`.
