# Local Engine Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When no whisper-server backend is reachable, `ws-cli batch` and `ws-cli watch` automatically fall back to spawning a local `ws-engine` subprocess and transcribing files directly through it.

**Architecture:** A new `LocalEngine` context manager in ws-cli spawns `uv run ws-engine` on a free local port, polls `/health` until ready, then POSTs audio files directly to `/transcribe`. Both `batch` and `watch` catch the `RuntimeError` from `WhisperClient.from_config()` and switch to this path. One prerequisite: `ws-engine/run.py` must read `PORT` from env instead of hardcoding 7001.

**Tech Stack:** Python `subprocess`, `httpx` (already a transitive dep via ws-client), `tempfile`, `atexit`, `typer`, `pytest` with `unittest.mock`.

---

## File Map

| File | Status | Change |
|---|---|---|
| `packages/ws-engine/src/ws_engine/run.py` | Modify | Read `PORT` from env via `config()` |
| `packages/ws-engine/tests/test_run.py` | Create | Test that `PORT` env var is respected |
| `apps/cli/src/ws_cli/local_engine.py` | Create | `LocalEngine` context manager |
| `apps/cli/tests/test_local_engine.py` | Create | Unit tests for `LocalEngine` |
| `apps/cli/src/ws_cli/batch.py` | Modify | Add fallback path + `--model`, `--language`, `--engine-timeout` |
| `apps/cli/tests/test_batch.py` | Modify | Add fallback tests |
| `apps/cli/src/ws_cli/watch.py` | Modify | Add fallback path + `--model`, `--language`, `--engine-timeout`, `--output-dir` |
| `apps/cli/tests/test_watch.py` | Modify | Add fallback tests |

---

## Task 1: ws-engine reads PORT from env

**Files:**
- Modify: `packages/ws-engine/src/ws_engine/run.py`
- Create: `packages/ws-engine/tests/test_run.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/ws-engine/tests/test_run.py
"""Test that the ws-engine entrypoint respects the PORT env var."""
import importlib
from unittest.mock import patch
import pytest


def test_run_uses_port_from_env(monkeypatch):
    """PORT env var should override the default 7001."""
    monkeypatch.setenv("PORT", "9876")
    monkeypatch.setenv("LOGLEVEL", "warning")

    import ws_engine.run as run_mod
    importlib.reload(run_mod)  # re-evaluates module-level config() with new env

    with patch("ws_engine.run.uvicorn.run") as mock_run:
        run_mod.main()

    _, kwargs = mock_run.call_args
    assert kwargs.get("port") == 9876


def test_run_defaults_to_7001(monkeypatch):
    """Without PORT set, default is 7001."""
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setenv("LOGLEVEL", "warning")

    import ws_engine.run as run_mod
    importlib.reload(run_mod)

    with patch("ws_engine.run.uvicorn.run") as mock_run:
        run_mod.main()

    _, kwargs = mock_run.call_args
    assert kwargs.get("port") == 7001
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest packages/ws-engine/tests/test_run.py -v
```

Expected: FAIL — `main()` passes `port=7001` hardcoded, not 9876.

- [ ] **Step 3: Implement the fix**

In `packages/ws-engine/src/ws_engine/run.py`, replace:

```python
def main():
    """Entry point for the ws-engine command."""
    uvicorn.run(app, host="0.0.0.0", port=7001, log_level=loglevel)
```

with:

```python
port: int = config("PORT", default=7001, cast=int)


def main():
    """Entry point for the ws-engine command."""
    uvicorn.run(app, host="0.0.0.0", port=port, log_level=loglevel)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest packages/ws-engine/tests/test_run.py -v
```

Expected: PASS both tests.

- [ ] **Step 5: Run full ws-engine test suite to check for regressions**

```bash
uv run pytest packages/ws-engine/tests/ -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add packages/ws-engine/src/ws_engine/run.py packages/ws-engine/tests/test_run.py
git commit -m "feat(ws-engine): read PORT from env var (default 7001)"
```

---

## Task 2: LocalEngine context manager

**Files:**
- Create: `apps/cli/src/ws_cli/local_engine.py`
- Create: `apps/cli/tests/test_local_engine.py`

- [ ] **Step 1: Write the failing tests**

```python
# apps/cli/tests/test_local_engine.py
"""Unit tests for LocalEngine — mocks subprocess and httpx, no real engine needed."""
import atexit
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call, ANY

import httpx
import pytest

from ws_cli.local_engine import LocalEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_process(returncode=None):
    """Fake Popen object. returncode=None means process is still alive."""
    proc = MagicMock(spec=subprocess.Popen)
    proc.poll.return_value = returncode
    proc.wait.return_value = 0
    return proc


def _make_healthy_http_client():
    """httpx.Client that returns 200 on GET /health."""
    client = MagicMock(spec=httpx.Client)
    health_resp = MagicMock()
    health_resp.status_code = 200
    transcribe_resp = MagicMock()
    transcribe_resp.json.return_value = {"text": "hello", "language": "de", "segments": []}
    client.get.return_value = health_resp
    client.post.return_value = transcribe_resp
    return client


# ---------------------------------------------------------------------------
# Port helper
# ---------------------------------------------------------------------------

def test_find_free_port_returns_integer():
    from ws_cli.local_engine import _find_free_port
    port = _find_free_port()
    assert isinstance(port, int)
    assert 1024 < port < 65536


# ---------------------------------------------------------------------------
# __enter__ / startup
# ---------------------------------------------------------------------------

def test_enter_spawns_subprocess_with_port_env(tmp_path):
    mock_proc = _make_mock_process()
    mock_http = _make_healthy_http_client()

    with (
        patch("ws_cli.local_engine._find_free_port", return_value=54321),
        patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        patch("httpx.Client", return_value=mock_http),
        patch("time.sleep"),
    ):
        engine = LocalEngine().__enter__()

    env_used = mock_popen.call_args.kwargs["env"]
    assert env_used["PORT"] == "54321"
    assert "ENGINE_API_KEY" not in env_used
    engine.__exit__(None, None, None)


def test_enter_injects_model_override(tmp_path):
    mock_proc = _make_mock_process()
    mock_http = _make_healthy_http_client()

    with (
        patch("ws_cli.local_engine._find_free_port", return_value=54322),
        patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        patch("httpx.Client", return_value=mock_http),
        patch("time.sleep"),
    ):
        engine = LocalEngine(model="large-v3").__enter__()

    env_used = mock_popen.call_args.kwargs["env"]
    assert env_used["DEFAULT_FASTWHISPER_MODEL"] == "large-v3"
    engine.__exit__(None, None, None)


def test_enter_raises_if_process_dies_before_health():
    dead_proc = _make_mock_process(returncode=1)
    mock_http = MagicMock(spec=httpx.Client)
    mock_http.get.side_effect = httpx.RequestError("refused")

    stderr_content = b"ImportError: no module named faster_whisper"

    with (
        patch("ws_cli.local_engine._find_free_port", return_value=54323),
        patch("subprocess.Popen", return_value=dead_proc),
        patch("httpx.Client", return_value=mock_http),
        patch("tempfile.TemporaryFile") as mock_tf,
        patch("time.sleep"),
        patch("time.monotonic", side_effect=[0, 1, 2]),
    ):
        fake_file = MagicMock()
        fake_file.read.return_value = stderr_content
        mock_tf.return_value = fake_file

        engine = LocalEngine(timeout=5.0)
        with pytest.raises(RuntimeError, match="faster_whisper"):
            engine.__enter__()


def test_enter_raises_on_health_timeout():
    alive_proc = _make_mock_process(returncode=None)
    mock_http = MagicMock(spec=httpx.Client)
    mock_http.get.side_effect = httpx.RequestError("refused")

    # monotonic() calls: once for deadline calc, then loop checks
    monotonic_values = [0.0] + [i * 1.0 for i in range(1, 20)]

    with (
        patch("ws_cli.local_engine._find_free_port", return_value=54324),
        patch("subprocess.Popen", return_value=alive_proc),
        patch("httpx.Client", return_value=mock_http),
        patch("tempfile.TemporaryFile"),
        patch("time.sleep"),
        patch("time.monotonic", side_effect=monotonic_values),
    ):
        engine = LocalEngine(timeout=5.0)
        with pytest.raises(RuntimeError, match="healthy"):
            engine.__enter__()


# ---------------------------------------------------------------------------
# transcribe()
# ---------------------------------------------------------------------------

def test_transcribe_posts_to_correct_url(tmp_path):
    audio = tmp_path / "test.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 36)  # minimal fake WAV

    mock_proc = _make_mock_process()
    mock_http = _make_healthy_http_client()

    with (
        patch("ws_cli.local_engine._find_free_port", return_value=54325),
        patch("subprocess.Popen", return_value=mock_proc),
        patch("httpx.Client", return_value=mock_http),
        patch("time.sleep"),
    ):
        with LocalEngine() as engine:
            result = engine.transcribe(audio, language="en")

    mock_http.post.assert_called_once()
    post_url = mock_http.post.call_args.args[0]
    assert "54325" in post_url
    assert "/transcribe" in post_url
    assert result["text"] == "hello"


def test_transcribe_sends_language(tmp_path):
    audio = tmp_path / "test.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 36)

    mock_proc = _make_mock_process()
    mock_http = _make_healthy_http_client()

    with (
        patch("ws_cli.local_engine._find_free_port", return_value=54326),
        patch("subprocess.Popen", return_value=mock_proc),
        patch("httpx.Client", return_value=mock_http),
        patch("time.sleep"),
    ):
        with LocalEngine() as engine:
            engine.transcribe(audio, language="fr")

    data_sent = mock_http.post.call_args.kwargs.get("data", {})
    assert data_sent.get("language") == "fr"


# ---------------------------------------------------------------------------
# __exit__ / shutdown
# ---------------------------------------------------------------------------

def test_exit_terminates_process():
    mock_proc = _make_mock_process()
    mock_http = _make_healthy_http_client()

    with (
        patch("ws_cli.local_engine._find_free_port", return_value=54327),
        patch("subprocess.Popen", return_value=mock_proc),
        patch("httpx.Client", return_value=mock_http),
        patch("time.sleep"),
    ):
        with LocalEngine():
            pass  # __exit__ called here

    mock_proc.terminate.assert_called_once()


def test_exit_kills_if_terminate_hangs():
    mock_proc = _make_mock_process()
    mock_proc.wait.side_effect = [subprocess.TimeoutExpired("uv", 10), None]
    mock_http = _make_healthy_http_client()

    with (
        patch("ws_cli.local_engine._find_free_port", return_value=54328),
        patch("subprocess.Popen", return_value=mock_proc),
        patch("httpx.Client", return_value=mock_http),
        patch("time.sleep"),
    ):
        with LocalEngine():
            pass

    mock_proc.terminate.assert_called_once()
    mock_proc.kill.assert_called_once()


def test_atexit_handler_unregistered_after_clean_exit():
    mock_proc = _make_mock_process()
    mock_http = _make_healthy_http_client()

    with (
        patch("ws_cli.local_engine._find_free_port", return_value=54329),
        patch("subprocess.Popen", return_value=mock_proc),
        patch("httpx.Client", return_value=mock_http),
        patch("time.sleep"),
        patch("atexit.register") as mock_reg,
        patch("atexit.unregister") as mock_unreg,
    ):
        engine = LocalEngine()
        with engine:
            registered_fn = mock_reg.call_args.args[0]

    mock_unreg.assert_called_once_with(registered_fn)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest apps/cli/tests/test_local_engine.py -v 2>&1 | head -30
```

Expected: ImportError — `ws_cli.local_engine` does not exist yet.

- [ ] **Step 3: Implement `local_engine.py`**

Create `apps/cli/src/ws_cli/local_engine.py`:

```python
"""LocalEngine — spawns a local ws-engine subprocess as a fallback transcription backend."""
import atexit
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


def _find_free_port() -> int:
    """Bind to port 0, let the OS assign a free port, return it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class LocalEngine:
    """Context manager that spawns a local ws-engine subprocess and transcribes via HTTP.

    Usage::

        with LocalEngine(model="small", timeout=120) as engine:
            result = engine.transcribe(Path("audio.wav"), language="de")
            print(result["text"])

    The subprocess is terminated on __exit__ (or via atexit on unclean exit).
    No replacements or initial_prompt are sent — local fallback mode only.
    """

    def __init__(self, model: str | None = None, timeout: float = 120.0) -> None:
        self.model = model
        self.timeout = timeout
        self._process: subprocess.Popen | None = None
        self._port: int | None = None
        self._stderr_file = None
        self._http: httpx.Client | None = None
        # Store as attribute before registering — atexit.unregister needs the same object.
        self._atexit_fn = self._shutdown

    # ------------------------------------------------------------------
    # Context manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> "LocalEngine":
        self._port = _find_free_port()

        env = os.environ.copy()
        env.pop("ENGINE_API_KEY", None)
        env["PORT"] = str(self._port)
        if self.model:
            env["DEFAULT_FASTWHISPER_MODEL"] = self.model

        self._stderr_file = tempfile.TemporaryFile()
        self._process = subprocess.Popen(
            ["uv", "run", "ws-engine"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=self._stderr_file,
        )
        self._http = httpx.Client(timeout=30.0)
        atexit.register(self._atexit_fn)

        self._wait_for_health()
        return self

    def __exit__(self, *args: object) -> None:
        self._shutdown()
        atexit.unregister(self._atexit_fn)
        if self._http:
            self._http.close()
        if self._stderr_file:
            self._stderr_file.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def transcribe(self, filepath: Path, language: str = "de") -> dict:
        """POST audio to /transcribe. Returns {text, language, segments}.

        initial_prompt and replacements are omitted — no DB in local fallback mode.
        The response never contains 'md'; callers should use 'text'.
        """
        with open(filepath, "rb") as f:
            resp = self._http.post(
                f"http://localhost:{self._port}/transcribe",
                files={"audio_file": (filepath.name, f, "audio/wav")},
                data={"language": language},
                timeout=300.0,
            )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _wait_for_health(self) -> None:
        url = f"http://localhost:{self._port}/health"
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                self._stderr_file.seek(0)
                stderr = self._stderr_file.read().decode(errors="replace")
                raise RuntimeError(f"Engine process exited early:\n{stderr}")
            try:
                r = self._http.get(url)
                if r.status_code == 200:
                    sys.stderr.write("\n")
                    sys.stderr.flush()
                    return
            except httpx.RequestError:
                pass
            sys.stderr.write(".")
            sys.stderr.flush()
            time.sleep(1.0)

        self._stderr_file.seek(0)
        stderr = self._stderr_file.read().decode(errors="replace")
        raise RuntimeError(
            f"Engine did not become healthy within {self.timeout}s:\n{stderr}"
        )

    def _shutdown(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest apps/cli/tests/test_local_engine.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add apps/cli/src/ws_cli/local_engine.py apps/cli/tests/test_local_engine.py
git commit -m "feat(ws-cli): add LocalEngine subprocess context manager"
```

---

## Task 3: batch.py fallback

**Files:**
- Modify: `apps/cli/src/ws_cli/batch.py`
- Modify: `apps/cli/tests/test_batch.py`

- [ ] **Step 1: Write the failing tests**

Append to `apps/cli/tests/test_batch.py`:

```python
# ── Fallback tests ────────────────────────────────────────────────────

def _make_local_engine(transcript="Transcribed text"):
    """Mock LocalEngine context manager."""
    engine = MagicMock()
    engine.transcribe.return_value = {"text": transcript, "language": "de", "segments": []}
    # Make it work as a context manager
    engine.__enter__ = lambda s: engine
    engine.__exit__ = MagicMock(return_value=False)
    return engine


def test_batch_fallback_used_when_no_server(tmp_path):
    """When from_config raises RuntimeError, LocalEngine is used instead."""
    make_wav(tmp_path / "audio.wav")
    mock_engine = _make_local_engine()

    with (
        patch("ws_client.client.WhisperClient.from_config", side_effect=RuntimeError("no server")),
        patch("ws_cli.batch.LocalEngine", return_value=mock_engine),
    ):
        result = runner.invoke(app, ["batch", str(tmp_path)])

    mock_engine.transcribe.assert_called_once()
    assert result.exit_code == 0


def test_batch_fallback_writes_text_to_audio_parent(tmp_path):
    """Fallback writes <stem>.txt next to the audio file when no --output-dir."""
    make_wav(tmp_path / "speech.wav")
    mock_engine = _make_local_engine(transcript="Hello world")

    with (
        patch("ws_client.client.WhisperClient.from_config", side_effect=RuntimeError("no server")),
        patch("ws_cli.batch.LocalEngine", return_value=mock_engine),
    ):
        runner.invoke(app, ["batch", str(tmp_path)])

    txt = tmp_path / "speech.txt"
    assert txt.exists()
    assert txt.read_text() == "Hello world"


def test_batch_fallback_respects_output_dir(tmp_path):
    """Fallback writes to --output-dir when provided."""
    make_wav(tmp_path / "speech.wav")
    out_dir = tmp_path / "out"
    mock_engine = _make_local_engine(transcript="Output text")

    with (
        patch("ws_client.client.WhisperClient.from_config", side_effect=RuntimeError("no server")),
        patch("ws_cli.batch.LocalEngine", return_value=mock_engine),
    ):
        runner.invoke(app, ["batch", str(tmp_path), "--output-dir", str(out_dir)])

    txt = out_dir / "speech.txt"
    assert txt.exists()
    assert txt.read_text() == "Output text"


def test_batch_fallback_passes_model_and_language(tmp_path):
    """--model and --language are forwarded to LocalEngine."""
    make_wav(tmp_path / "audio.wav")
    mock_engine = _make_local_engine()

    with (
        patch("ws_client.client.WhisperClient.from_config", side_effect=RuntimeError("no server")),
        patch("ws_cli.batch.LocalEngine", return_value=mock_engine) as mock_le_cls,
    ):
        runner.invoke(app, ["batch", str(tmp_path), "--model", "large-v3", "--language", "en"])

    mock_le_cls.assert_called_once()
    call_kwargs = mock_le_cls.call_args.kwargs
    assert call_kwargs.get("model") == "large-v3"
    engine_transcribe_kwargs = mock_engine.transcribe.call_args.kwargs
    assert engine_transcribe_kwargs.get("language") == "en"


def test_batch_warns_when_model_flag_with_live_server(tmp_path):
    """--model should print a warning and be ignored when server is reachable."""
    make_wav(tmp_path / "audio.wav")
    mock_client = make_client()

    with patch("ws_client.client.WhisperClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["batch", str(tmp_path), "--model", "large-v3"])

    assert "ignored" in result.output.lower() or "ignored" in (result.stderr or "").lower()
    mock_client.submit_job.assert_called_once()


def test_batch_fallback_continues_on_per_file_error(tmp_path):
    """A transcription error on one file does not abort processing of others."""
    make_wav(tmp_path / "a.wav")
    make_wav(tmp_path / "b.wav")
    mock_engine = _make_local_engine()
    mock_engine.transcribe.side_effect = [
        httpx.RequestError("connection refused"),
        {"text": "second file ok", "language": "de", "segments": []},
    ]

    with (
        patch("ws_client.client.WhisperClient.from_config", side_effect=RuntimeError("no server")),
        patch("ws_cli.batch.LocalEngine", return_value=mock_engine),
    ):
        result = runner.invoke(app, ["batch", str(tmp_path)])

    assert mock_engine.transcribe.call_count == 2
    assert result.exit_code == 0
```

Note: add `import httpx` near the top of `test_batch.py`.

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest apps/cli/tests/test_batch.py -k "fallback" -v 2>&1 | head -40
```

Expected: ImportError or AttributeError — fallback logic not yet in `batch.py`.

- [ ] **Step 3: Implement the fallback in `batch.py`**

Replace `apps/cli/src/ws_cli/batch.py` with:

```python
from pathlib import Path
from typing import Optional
import typer
import httpx

EXTENSIONS = {"wav", "webm", "flac", "mp3", "m4a", "ogg", "aac"}


def batch_transcribe(
    directory: Path = typer.Argument(..., help="Directory of audio files to transcribe."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Include subdirectories."),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir", help="Directory to write transcripts."),
    model: Optional[str] = typer.Option(None, "--model", help="Whisper model name (local fallback only)."),
    language: str = typer.Option("de", "--language", help="Language hint for transcription (local fallback only)."),
    engine_timeout: float = typer.Option(120.0, "--engine-timeout", help="Seconds to wait for local engine startup (local fallback only)."),
):
    """Transcribe all audio files in a directory (submit + wait for results)."""
    from ws_client.client import WhisperClient

    glob_fn = directory.rglob if recursive else directory.glob
    files = [f for ext in EXTENSIONS for f in glob_fn(f"*.{ext}")]

    try:
        client = WhisperClient.from_config()
    except RuntimeError:
        _batch_local_fallback(files, output_dir, model, language, engine_timeout)
        return

    if model is not None:
        typer.echo(
            "--model is only used in local fallback mode and will be ignored.",
            err=True,
        )

    jobs: list[tuple[Path, int]] = []
    for f in files:
        try:
            result = client.submit_job(f)
            job_id = result["id"]
            jobs.append((f, job_id))
            print(f"Submitted {f.name} -> job {job_id}")
        except Exception as e:
            print(f"Failed to submit {f.name}: {e}")

    if not jobs:
        print("No audio files found.")
        return

    print(f"\nWaiting for {len(jobs)} job(s) to complete...")

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    for filepath, job_id in jobs:
        try:
            job = client.wait_for_job(job_id)
            job_status = job.get("status", "unknown")
            print(f"Completed {filepath.name}: {job_status}")
            if output_dir and job_status == "completed":
                transcript = job.get("transcript", "") or job.get("md", "")
                out_path = output_dir / f"{filepath.stem}.txt"
                out_path.write_text(transcript, encoding="utf-8")
                print(f"  -> Saved to {out_path}")
        except TimeoutError:
            print(f"Timeout waiting for job {job_id} ({filepath.name})")
        except Exception as e:
            print(f"Error for job {job_id} ({filepath.name}): {e}")


def _batch_local_fallback(
    files: list[Path],
    output_dir: Optional[Path],
    model: Optional[str],
    language: str,
    engine_timeout: float,
) -> None:
    from .local_engine import LocalEngine

    if not files:
        print("No audio files found.")
        return

    typer.echo(
        "No server reachable — starting local engine "
        "(replacements, prompts, and language selection not available on server path).",
        err=True,
    )

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    with LocalEngine(model=model, timeout=engine_timeout) as engine:
        for filepath in files:
            try:
                result = engine.transcribe(filepath, language=language)
                transcript = result.get("text", "")
                out_path = (output_dir or filepath.parent) / f"{filepath.stem}.txt"
                out_path.write_text(transcript, encoding="utf-8")
                print(f"Transcribed {filepath.name} -> {out_path}")
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                typer.echo(f"Failed to transcribe {filepath.name}: {e}", err=True)
```

- [ ] **Step 4: Run all batch tests**

```bash
uv run pytest apps/cli/tests/test_batch.py -v
```

Expected: all tests pass (including existing server-path tests).

- [ ] **Step 5: Commit**

```bash
git add apps/cli/src/ws_cli/batch.py apps/cli/tests/test_batch.py
git commit -m "feat(ws-cli): add local engine fallback to batch command"
```

---

## Task 4: watch.py fallback

**Files:**
- Modify: `apps/cli/src/ws_cli/watch.py`
- Modify: `apps/cli/tests/test_watch.py`

- [ ] **Step 1: Write the failing tests**

Append to `apps/cli/tests/test_watch.py`:

```python
# ── Fallback tests ────────────────────────────────────────────────────
import httpx


def _make_local_engine(transcript="Watched text"):
    engine = MagicMock()
    engine.transcribe.return_value = {"text": transcript, "language": "de", "segments": []}
    engine.__enter__ = lambda s: engine
    engine.__exit__ = MagicMock(return_value=False)
    return engine


def test_watch_fallback_used_when_no_server(tmp_path):
    """When from_config raises RuntimeError, LocalEngine is used."""
    audio_file = make_wav(tmp_path / "test.wav")
    mock_engine = _make_local_engine()

    call_count = 0

    def fake_sleep(_):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise KeyboardInterrupt

    with (
        patch("ws_client.client.WhisperClient.from_config", side_effect=RuntimeError("no server")),
        patch("ws_cli.watch.LocalEngine", return_value=mock_engine),
        patch("ws_cli.watch.time.sleep", side_effect=fake_sleep),
    ):
        runner.invoke(app, ["watch", str(tmp_path)])

    mock_engine.transcribe.assert_called_once()


def test_watch_fallback_writes_txt_next_to_audio(tmp_path):
    """Fallback writes <stem>.txt next to audio file."""
    make_wav(tmp_path / "speech.wav")
    mock_engine = _make_local_engine(transcript="Watch result")

    call_count = 0

    def fake_sleep(_):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise KeyboardInterrupt

    with (
        patch("ws_client.client.WhisperClient.from_config", side_effect=RuntimeError("no server")),
        patch("ws_cli.watch.LocalEngine", return_value=mock_engine),
        patch("ws_cli.watch.time.sleep", side_effect=fake_sleep),
    ):
        runner.invoke(app, ["watch", str(tmp_path)])

    txt = tmp_path / "speech.txt"
    assert txt.exists()
    assert txt.read_text() == "Watch result"


def test_watch_fallback_respects_output_dir(tmp_path):
    """Fallback writes to --output-dir when provided."""
    make_wav(tmp_path / "speech.wav")
    out_dir = tmp_path / "out"
    mock_engine = _make_local_engine(transcript="Output dir result")

    call_count = 0

    def fake_sleep(_):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise KeyboardInterrupt

    with (
        patch("ws_client.client.WhisperClient.from_config", side_effect=RuntimeError("no server")),
        patch("ws_cli.watch.LocalEngine", return_value=mock_engine),
        patch("ws_cli.watch.time.sleep", side_effect=fake_sleep),
    ):
        runner.invoke(app, ["watch", str(tmp_path), "--output-dir", str(out_dir)])

    txt = out_dir / "speech.txt"
    assert txt.exists()


def test_watch_fallback_continues_on_per_file_error(tmp_path):
    """A transcription error does not abort the watch loop."""
    make_wav(tmp_path / "a.wav")
    make_wav(tmp_path / "b.wav")

    call_count = 0
    transcribe_count = 0

    mock_engine = _make_local_engine()
    mock_engine.transcribe.side_effect = [
        httpx.RequestError("refused"),
        {"text": "ok", "language": "de", "segments": []},
    ]

    def fake_sleep(_):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise KeyboardInterrupt

    with (
        patch("ws_client.client.WhisperClient.from_config", side_effect=RuntimeError("no server")),
        patch("ws_cli.watch.LocalEngine", return_value=mock_engine),
        patch("ws_cli.watch.time.sleep", side_effect=fake_sleep),
    ):
        result = runner.invoke(app, ["watch", str(tmp_path)])

    assert mock_engine.transcribe.call_count == 2
    assert result.exit_code == 0


def test_watch_fallback_passes_model_and_language(tmp_path):
    """--model and --language are forwarded correctly."""
    make_wav(tmp_path / "audio.wav")
    mock_engine = _make_local_engine()

    call_count = 0

    def fake_sleep(_):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise KeyboardInterrupt

    with (
        patch("ws_client.client.WhisperClient.from_config", side_effect=RuntimeError("no server")),
        patch("ws_cli.watch.LocalEngine", return_value=mock_engine) as mock_le_cls,
        patch("ws_cli.watch.time.sleep", side_effect=fake_sleep),
    ):
        runner.invoke(app, ["watch", str(tmp_path), "--model", "small", "--language", "fr"])

    call_kwargs = mock_le_cls.call_args.kwargs
    assert call_kwargs.get("model") == "small"
    transcribe_kwargs = mock_engine.transcribe.call_args.kwargs
    assert transcribe_kwargs.get("language") == "fr"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest apps/cli/tests/test_watch.py -k "fallback" -v 2>&1 | head -40
```

Expected: AttributeError or ImportError — fallback logic not yet in `watch.py`.

- [ ] **Step 3: Implement the fallback in `watch.py`**

Replace `apps/cli/src/ws_cli/watch.py` with:

```python
import time
from pathlib import Path
from typing import Optional
import typer
import httpx

EXTENSIONS = {"wav", "webm", "flac", "mp3", "m4a", "ogg", "aac"}


def watch_directory(
    directory: Path = typer.Argument(..., help="Directory to watch for new audio files."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Watch subdirectories too."),
    poll_interval: float = typer.Option(1.0, "--poll-interval", help="Seconds between directory scans."),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir", help="Directory to write transcripts."),
    model: Optional[str] = typer.Option(None, "--model", help="Whisper model name (local fallback only)."),
    language: str = typer.Option("de", "--language", help="Language hint for transcription (local fallback only)."),
    engine_timeout: float = typer.Option(120.0, "--engine-timeout", help="Seconds to wait for local engine startup (local fallback only)."),
):
    """Watch a directory for new audio files and submit them for transcription."""
    from ws_client.client import WhisperClient

    try:
        client = WhisperClient.from_config()
    except RuntimeError:
        _watch_local_fallback(
            directory, recursive, poll_interval, output_dir, model, language, engine_timeout
        )
        return

    if model is not None:
        typer.echo(
            "--model is only used in local fallback mode and will be ignored.",
            err=True,
        )

    seen: set[Path] = set()
    print(f"Watching {directory} for audio files (recursive={recursive})...")

    while True:
        glob_fn = directory.rglob if recursive else directory.glob
        for ext in EXTENSIONS:
            for f in glob_fn(f"*.{ext}"):
                if f not in seen:
                    seen.add(f)
                    try:
                        result = client.submit_job(f)
                        print(f"Submitted {f.name} -> job {result['id']}")
                    except Exception as e:
                        print(f"Failed to submit {f.name}: {e}")
        time.sleep(poll_interval)


def _watch_local_fallback(
    directory: Path,
    recursive: bool,
    poll_interval: float,
    output_dir: Optional[Path],
    model: Optional[str],
    language: str,
    engine_timeout: float,
) -> None:
    from .local_engine import LocalEngine

    typer.echo(
        "No server reachable — starting local engine (replacements and prompts not available).",
        err=True,
    )

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    seen: set[Path] = set()
    print(f"Watching {directory} for audio files (local fallback, recursive={recursive})...")

    with LocalEngine(model=model, timeout=engine_timeout) as engine:
        while True:
            glob_fn = directory.rglob if recursive else directory.glob
            for ext in EXTENSIONS:
                for f in glob_fn(f"*.{ext}"):
                    if f not in seen:
                        seen.add(f)
                        try:
                            result = engine.transcribe(f, language=language)
                            transcript = result.get("text", "")
                            out_path = (output_dir or f.parent) / f"{f.stem}.txt"
                            out_path.write_text(transcript, encoding="utf-8")
                            print(f"Transcribed {f.name} -> {out_path}")
                        except (httpx.HTTPStatusError, httpx.RequestError) as e:
                            typer.echo(f"Failed to transcribe {f.name}: {e}", err=True)
            time.sleep(poll_interval)
```

- [ ] **Step 4: Run all watch tests**

```bash
uv run pytest apps/cli/tests/test_watch.py -v
```

Expected: all tests pass (including existing server-path tests).

- [ ] **Step 5: Run full CLI test suite**

```bash
uv run pytest apps/cli/tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add apps/cli/src/ws_cli/watch.py apps/cli/tests/test_watch.py
git commit -m "feat(ws-cli): add local engine fallback to watch command"
```

---

## Task 5: Final verification

- [ ] **Step 1: Run full project test suite**

```bash
uv run pytest -v
```

Expected: all tests pass. Note: ws-engine tests may require mocked transcriber; that is already in place per existing test patterns.

- [ ] **Step 2: Smoke test the new CLI options appear**

```bash
uv run ws-cli batch --help
uv run ws-cli watch --help
```

Expected output includes: `--model`, `--language`, `--engine-timeout` for both; `--output-dir` for watch.

- [ ] **Step 3: Commit final verification**

```bash
git add -p  # stage any fixups discovered in step 1
git commit -m "fix(ws-cli): local engine fallback fixups" || echo "Nothing to commit — all clean"
```

If step 1 was fully clean with no changes needed, skip this step.
