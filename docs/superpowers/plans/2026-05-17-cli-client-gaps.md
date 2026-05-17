# CLI & Client Gaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the three new gateway routes into the client library and CLI, simplify `resona transcribe` to use the sync gateway, and add `resona submit`, `resona speech`, and `resona engines status` commands.

**Architecture:** `ResonaClient` gains `list_engines`, `create_transcription`, and `create_speech` methods; `resona transcribe` is simplified to POST to `POST /v1/audio/transcriptions` (gateway owns routing) with a local-engine fallback on connection failure; new `submit.py` and `speech.py` modules add the two new commands; `engines.py` gains a `status` subcommand.

**Tech Stack:** Python 3.12, Typer, httpx + respx, pytest, Typer CliRunner, rich (transitive dep via Textual)

---

## File Structure

**Created:**
- `apps/resona-cli/src/resona_cli/submit.py` — `resona submit` command
- `apps/resona-cli/src/resona_cli/speech.py` — `resona speech` command
- `apps/resona-cli/tests/test_submit.py` — tests for submit command
- `apps/resona-cli/tests/test_speech.py` — tests for speech command

**Modified:**
- `packages/client/src/resona_client/client.py` — add `list_engines`, `create_transcription`, `create_speech`; update `submit_job` to accept `engine`
- `packages/client/tests/test_client.py` — add 3 tests for new methods
- `apps/resona-cli/src/resona_cli/transcribe.py` — rewrite to gateway-first, remove cloud-direct and config-resolution paths
- `apps/resona-cli/tests/test_transcribe.py` — update for new routing
- `apps/resona-cli/src/resona_cli/main.py` — register `submit` and `speech` commands
- `apps/resona-cli/src/resona_cli/engines.py` — add `status` subcommand
- `apps/resona-cli/tests/test_engines.py` — add test for `status`
- `justfile` — add per-profile docker targets, `format`, `lint`

---

## Task 1: Client library — three new methods

**Files:**
- Modify: `packages/client/src/resona_client/client.py`
- Modify: `packages/client/tests/test_client.py`

- [ ] **Step 1: Read current client.py and test_client.py**

  Run: `cat packages/client/src/resona_client/client.py packages/client/tests/test_client.py`

- [ ] **Step 2: Write three failing tests**

  Append to `packages/client/tests/test_client.py` (after the last `# ──` block):

  ```python
  # ── v1 Audio & Engine routes ──────────────────────────────────────────────────

  def test_list_engines(client):
      with respx.mock:
          respx.get(f"{BASE}/v1/engines").mock(
              return_value=httpx.Response(200, json={
                  "engines": [{"name": "faster-whisper", "kind": "local",
                                "capabilities": ["stt"], "private": True,
                                "available": True, "models": ["large-v3"],
                                "url": "http://localhost:7001", "provider": None}],
                  "default": "faster-whisper",
              })
          )
          result = client.list_engines()
      assert "engines" in result
      assert result["engines"][0]["name"] == "faster-whisper"


  def test_create_transcription(client, audio_file):
      with respx.mock:
          respx.post(f"{BASE}/v1/audio/transcriptions").mock(
              return_value=httpx.Response(200, json={
                  "text": "hello world", "language": "de", "segments": []
              })
          )
          result = client.create_transcription(audio_file, language="de")
      assert result["text"] == "hello world"


  def test_create_speech(client):
      fake_audio = b"\xff\xfb\x90\x00" * 100  # fake mp3 bytes
      with respx.mock:
          respx.post(f"{BASE}/v1/audio/speech").mock(
              return_value=httpx.Response(200, content=fake_audio,
                                          headers={"content-type": "audio/mpeg"})
          )
          result = client.create_speech("hello", voice="nova")
      assert isinstance(result, bytes)
      assert len(result) > 0
  ```

- [ ] **Step 3: Run tests to verify they fail**

  Run: `uv run pytest packages/client/tests/test_client.py::test_list_engines packages/client/tests/test_client.py::test_create_transcription packages/client/tests/test_client.py::test_create_speech -v`
  Expected: FAIL — `AttributeError: 'ResonaClient' object has no attribute 'list_engines'`

- [ ] **Step 4: Add the three methods to `client.py`**

  Add after the `wait_for_job` method (before `# ── Replacement CRUD`). Also update `submit_job` to accept `engine`:

  First update `submit_job` — add `engine: Optional[str] = None` parameter and pass it in `data`:

  ```python
  def submit_job(
      self,
      filepath: Path | str,
      keep: bool = True,
      translate: bool = False,
      engine: Optional[str] = None,
  ) -> dict:
      """Upload an audio file and register it for async transcription. POST /jobs"""
      filepath = Path(filepath)
      data: dict = {"keep": str(keep).lower(), "translate": str(translate).lower()}
      if engine:
          data["engine"] = engine
      with open(filepath, "rb") as f:
          resp = self._client.post(
              f"{self.base_url}/jobs",
              files={"audio_files": (filepath.name, f, "audio/wav")},
              data=data,
          )
      resp.raise_for_status()
      jobs = resp.json()
      return jobs[0] if isinstance(jobs, list) else jobs
  ```

  Then add the three new methods (add after `wait_for_job`, before `# ── Replacement CRUD`):

  ```python
  # ── v1 Audio & Engine routes ──────────────────────────────────────────────────

  def list_engines(self) -> dict:
      """List every engine the gateway exposes, with capabilities and status. GET /v1/engines"""
      resp = self._client.get(f"{self.base_url}/v1/engines")
      resp.raise_for_status()
      return resp.json()

  def create_transcription(
      self,
      audio_path: "Path | str",
      *,
      model: str = "whisper-1",
      language: Optional[str] = None,
      prompt: Optional[str] = None,
      response_format: str = "json",
      engine: Optional[str] = None,
      private: bool = False,
  ) -> dict:
      """Transcribe audio synchronously via the gateway. POST /v1/audio/transcriptions

      Returns:
          Dict with keys ``text``, ``language``, ``segments``.
      """
      audio_path = Path(audio_path)
      data: dict = {
          "model": model,
          "response_format": response_format,
          "private": str(private).lower(),
      }
      if language:
          data["language"] = language
      if prompt:
          data["prompt"] = prompt
      if engine:
          data["engine"] = engine
      with open(audio_path, "rb") as f:
          resp = self._client.post(
              f"{self.base_url}/v1/audio/transcriptions",
              files={"file": (audio_path.name, f, "audio/wav")},
              data=data,
          )
      resp.raise_for_status()
      return resp.json()

  def create_speech(
      self,
      text: str,
      *,
      model: str = "tts-1",
      voice: str = "alloy",
      response_format: str = "mp3",
      speed: float = 1.0,
      engine: Optional[str] = None,
      private: bool = False,
  ) -> bytes:
      """Synthesise speech from text via the gateway. POST /v1/audio/speech

      Returns:
          Raw audio bytes in the requested format.
      """
      body: dict = {
          "input": text,
          "model": model,
          "voice": voice,
          "response_format": response_format,
          "speed": speed,
          "private": private,
      }
      if engine:
          body["engine"] = engine
      resp = self._client.post(
          f"{self.base_url}/v1/audio/speech",
          json=body,
      )
      resp.raise_for_status()
      return resp.content
  ```

- [ ] **Step 5: Run tests to verify they pass**

  Run: `uv run pytest packages/client/tests/test_client.py -v`
  Expected: all PASS.

- [ ] **Step 6: Commit**

  ```bash
  git add packages/client/src/resona_client/client.py packages/client/tests/test_client.py
  git commit -m "feat(client): add list_engines, create_transcription, create_speech; engine param on submit_job"
  ```

---

## Task 2: `resona transcribe` redesign — gateway-first

**Files:**
- Modify: `apps/resona-cli/src/resona_cli/transcribe.py`
- Modify: `apps/resona-cli/tests/test_transcribe.py`

The current `transcribe.py` has three routing branches: cloud-direct, resona-api (job queue), local fallback. The new design collapses cloud-direct and server branches into a single gateway call (`create_transcription`), retaining only the local fallback for when the server is unreachable.

- [ ] **Step 1: Inspect transcribe.py and test_transcribe.py**

  Run: `cat apps/resona-cli/src/resona_cli/transcribe.py apps/resona-cli/tests/test_transcribe.py`

- [ ] **Step 2: Write the new failing tests**

  Replace `apps/resona-cli/tests/test_transcribe.py` entirely with the following. The new tests keep `_expand_inputs` coverage and fallback coverage, but replace the old server-path tests with gateway-path tests:

  ```python
  """Tests for resona_cli.transcribe.transcribe_files."""
  import io
  import struct
  import wave
  from pathlib import Path
  from unittest.mock import MagicMock, patch

  import httpx
  import pytest
  from typer.testing import CliRunner

  from resona_cli.main import app

  runner = CliRunner()


  def make_wav(path: Path) -> Path:
      buf = io.BytesIO()
      with wave.open(buf, "wb") as w:
          w.setnchannels(1)
          w.setsampwidth(2)
          w.setframerate(16000)
          w.writeframes(struct.pack("<" + "h" * 160, *([0] * 160)))
      path.write_bytes(buf.getvalue())
      return path


  def _make_gateway_client(text="Hello world"):
      client = MagicMock()
      client.create_transcription.return_value = {
          "text": text, "language": "de", "segments": []
      }
      return client


  def _noop_pipeline():
      p = MagicMock()
      p.run.side_effect = lambda t: t
      return p


  # ── Gateway path ──────────────────────────────────────────────────────────────

  def test_transcribe_no_files(tmp_path):
      result = runner.invoke(app, ["transcribe", str(tmp_path)])
      assert "No audio files found" in result.output


  def test_transcribe_gateway_called_for_single_file(tmp_path):
      f = make_wav(tmp_path / "a.wav")
      mock_client = _make_gateway_client()

      with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
          result = runner.invoke(app, ["transcribe", str(f)])

      mock_client.create_transcription.assert_called_once()
      assert result.exit_code == 0


  def test_transcribe_gateway_called_for_directory(tmp_path):
      make_wav(tmp_path / "a.wav")
      make_wav(tmp_path / "b.wav")
      mock_client = _make_gateway_client()

      with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
          runner.invoke(app, ["transcribe", str(tmp_path)])

      assert mock_client.create_transcription.call_count == 2


  def test_transcribe_gateway_writes_output_files(tmp_path):
      make_wav(tmp_path / "audio.wav")
      out_dir = tmp_path / "out"
      mock_client = _make_gateway_client(text="transcribed text")

      with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
          runner.invoke(app, ["transcribe", str(tmp_path), "--output-dir", str(out_dir)])

      txt_files = list(out_dir.glob("*.txt"))
      assert len(txt_files) == 1
      assert txt_files[0].read_text() == "transcribed text"


  def test_transcribe_gateway_forwards_engine_flag(tmp_path):
      make_wav(tmp_path / "a.wav")
      mock_client = _make_gateway_client()

      with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
          runner.invoke(app, ["transcribe", str(tmp_path), "--engine", "deepgram"])

      call_kwargs = mock_client.create_transcription.call_args.kwargs
      assert call_kwargs.get("engine") == "deepgram"


  def test_transcribe_gateway_forwards_private_flag(tmp_path):
      make_wav(tmp_path / "a.wav")
      mock_client = _make_gateway_client()

      with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
          runner.invoke(app, ["transcribe", str(tmp_path), "--private"])

      call_kwargs = mock_client.create_transcription.call_args.kwargs
      assert call_kwargs.get("private") is True


  def test_transcribe_gateway_forwards_model_flag(tmp_path):
      make_wav(tmp_path / "a.wav")
      mock_client = _make_gateway_client()

      with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
          runner.invoke(app, ["transcribe", str(tmp_path), "--model", "large-v3"])

      call_kwargs = mock_client.create_transcription.call_args.kwargs
      assert call_kwargs.get("model") == "large-v3"


  def test_transcribe_deduplicates_overlapping_inputs(tmp_path):
      f = make_wav(tmp_path / "a.wav")
      mock_client = _make_gateway_client()

      with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
          runner.invoke(app, ["transcribe", str(f), str(tmp_path)])

      assert mock_client.create_transcription.call_count == 1


  def test_transcribe_glob_filters_non_audio(tmp_path, monkeypatch):
      make_wav(tmp_path / "a.wav")
      (tmp_path / "b.txt").write_text("nope")
      monkeypatch.chdir(tmp_path)
      mock_client = _make_gateway_client()

      with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
          runner.invoke(app, ["transcribe", "*"])

      assert mock_client.create_transcription.call_count == 1


  def test_transcribe_missing_path_warns(tmp_path):
      mock_client = _make_gateway_client()
      with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
          result = runner.invoke(app, ["transcribe", str(tmp_path / "nope.wav")])
      assert "Not found" in result.output
      mock_client.create_transcription.assert_not_called()


  def test_transcribe_gateway_http_error_per_file_continues(tmp_path):
      make_wav(tmp_path / "a.wav")
      make_wav(tmp_path / "b.wav")
      mock_client = MagicMock()
      mock_client.create_transcription.side_effect = [
          httpx.HTTPStatusError("bad", request=MagicMock(), response=MagicMock()),
          {"text": "ok", "language": "de", "segments": []},
      ]

      with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
          result = runner.invoke(app, ["transcribe", str(tmp_path)])

      assert mock_client.create_transcription.call_count == 2
      assert result.exit_code == 0


  # ── Fallback path (no gateway) ────────────────────────────────────────────────

  def test_transcribe_fallback_used_when_no_server(tmp_path):
      make_wav(tmp_path / "audio.wav")
      mock_engine = MagicMock()
      mock_engine.transcribe.return_value = {"text": "hi", "language": "de", "segments": []}
      mock_engine.__enter__ = lambda s: mock_engine
      mock_engine.__exit__ = MagicMock(return_value=False)

      with (
          patch("resona_client.client.ResonaClient.from_config",
                side_effect=RuntimeError("no server")),
          patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
          patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine),
          patch("resona_postprocess.sources.build_pipeline_from_config",
                return_value=_noop_pipeline()),
      ):
          result = runner.invoke(app, ["transcribe", str(tmp_path)])

      mock_engine.transcribe.assert_called_once()
      assert result.exit_code == 0


  def test_transcribe_fallback_on_connect_error(tmp_path):
      make_wav(tmp_path / "audio.wav")
      mock_engine = MagicMock()
      mock_engine.transcribe.return_value = {"text": "local", "language": "de", "segments": []}
      mock_engine.__enter__ = lambda s: mock_engine
      mock_engine.__exit__ = MagicMock(return_value=False)

      mock_client = MagicMock()
      mock_client.create_transcription.side_effect = httpx.ConnectError("refused")

      with (
          patch("resona_client.client.ResonaClient.from_config", return_value=mock_client),
          patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
          patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine),
          patch("resona_postprocess.sources.build_pipeline_from_config",
                return_value=_noop_pipeline()),
      ):
          result = runner.invoke(app, ["transcribe", str(tmp_path)])

      mock_engine.transcribe.assert_called_once()
      assert result.exit_code == 0


  def test_transcribe_fallback_writes_text_next_to_audio(tmp_path):
      make_wav(tmp_path / "speech.wav")
      mock_engine = MagicMock()
      mock_engine.transcribe.return_value = {"text": "Hello world", "language": "de", "segments": []}
      mock_engine.__enter__ = lambda s: mock_engine
      mock_engine.__exit__ = MagicMock(return_value=False)

      with (
          patch("resona_client.client.ResonaClient.from_config",
                side_effect=RuntimeError("no server")),
          patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
          patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine),
          patch("resona_postprocess.sources.build_pipeline_from_config",
                return_value=_noop_pipeline()),
      ):
          runner.invoke(app, ["transcribe", str(tmp_path)])

      assert (tmp_path / "speech.txt").read_text() == "Hello world"


  def test_transcribe_fallback_respects_output_dir(tmp_path):
      make_wav(tmp_path / "speech.wav")
      out_dir = tmp_path / "out"
      mock_engine = MagicMock()
      mock_engine.transcribe.return_value = {"text": "Output text", "language": "de", "segments": []}
      mock_engine.__enter__ = lambda s: mock_engine
      mock_engine.__exit__ = MagicMock(return_value=False)

      with (
          patch("resona_client.client.ResonaClient.from_config",
                side_effect=RuntimeError("no server")),
          patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
          patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine),
          patch("resona_postprocess.sources.build_pipeline_from_config",
                return_value=_noop_pipeline()),
      ):
          runner.invoke(app, ["transcribe", str(tmp_path), "--output-dir", str(out_dir)])

      assert (out_dir / "speech.txt").read_text() == "Output text"


  def test_transcribe_fallback_passes_model_and_language(tmp_path):
      make_wav(tmp_path / "audio.wav")
      mock_engine = MagicMock()
      mock_engine.transcribe.return_value = {"text": "x", "language": "de", "segments": []}
      mock_engine.__enter__ = lambda s: mock_engine
      mock_engine.__exit__ = MagicMock(return_value=False)

      with (
          patch("resona_client.client.ResonaClient.from_config",
                side_effect=RuntimeError("no server")),
          patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
          patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine) as mock_le_cls,
          patch("resona_postprocess.sources.build_pipeline_from_config",
                return_value=_noop_pipeline()),
      ):
          runner.invoke(app, ["transcribe", str(tmp_path),
                               "--model", "large-v3", "--language", "en"])

      mock_le_cls.assert_called_once()
      assert mock_le_cls.call_args.kwargs.get("model") == "large-v3"
      assert mock_engine.transcribe.call_args.kwargs.get("language") == "en"


  def test_transcribe_fallback_builtin_engine_forwarded(tmp_path):
      make_wav(tmp_path / "audio.wav")
      mock_engine = MagicMock()
      mock_engine.transcribe.return_value = {"text": "x", "language": "de", "segments": []}
      mock_engine.__enter__ = lambda s: mock_engine
      mock_engine.__exit__ = MagicMock(return_value=False)

      with (
          patch("resona_client.client.ResonaClient.from_config",
                side_effect=RuntimeError("no server")),
          patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
          patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine) as mock_le_cls,
          patch("resona_postprocess.sources.build_pipeline_from_config",
                return_value=_noop_pipeline()),
      ):
          runner.invoke(app, ["transcribe", str(tmp_path), "--engine", "whisper"])

      assert mock_le_cls.call_args.kwargs.get("engine") == "whisper"


  def test_transcribe_fallback_non_builtin_engine_uses_default(tmp_path):
      """When --engine names a cloud entry and no gateway, fall back to default local engine."""
      make_wav(tmp_path / "audio.wav")
      mock_engine = MagicMock()
      mock_engine.transcribe.return_value = {"text": "x", "language": "de", "segments": []}
      mock_engine.__enter__ = lambda s: mock_engine
      mock_engine.__exit__ = MagicMock(return_value=False)

      from resona_client.config import EngineConfig
      mock_cfg = EngineConfig(engines=[], default_engine="voxtral")

      with (
          patch("resona_client.client.ResonaClient.from_config",
                side_effect=RuntimeError("no server")),
          patch("resona_client.config.EngineConfig.load", return_value=mock_cfg),
          patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
          patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine) as mock_le_cls,
          patch("resona_postprocess.sources.build_pipeline_from_config",
                return_value=_noop_pipeline()),
      ):
          runner.invoke(app, ["transcribe", str(tmp_path), "--engine", "deepgram"])

      assert mock_le_cls.call_args.kwargs.get("engine") == "voxtral"


  def test_transcribe_fallback_applies_postprocess_pipeline(tmp_path):
      make_wav(tmp_path / "audio.wav")
      out_dir = tmp_path / "out"
      mock_engine = MagicMock()
      mock_engine.transcribe.return_value = {"text": "hello", "language": "de", "segments": []}
      mock_engine.__enter__ = lambda s: mock_engine
      mock_engine.__exit__ = MagicMock(return_value=False)
      mock_pipeline = MagicMock()
      mock_pipeline.run.return_value = "HELLO"

      with (
          patch("resona_client.client.ResonaClient.from_config",
                side_effect=RuntimeError("no server")),
          patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
          patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine),
          patch("resona_postprocess.sources.build_pipeline_from_config",
                return_value=mock_pipeline),
      ):
          runner.invoke(app, ["transcribe", str(tmp_path), "--output-dir", str(out_dir)])

      mock_pipeline.run.assert_called_once_with("hello")
      assert (out_dir / "audio.txt").read_text() == "HELLO"


  def test_transcribe_uses_in_process_engine_when_available(tmp_path):
      make_wav(tmp_path / "audio.wav")
      out_dir = tmp_path / "out"
      mock_engine = MagicMock()
      mock_engine.transcribe.return_value = {"text": "hi", "language": "de", "segments": []}

      with (
          patch("resona_client.client.ResonaClient.from_config",
                side_effect=RuntimeError("no server")),
          patch("resona_cli.transcribe.InProcessEngine", return_value=mock_engine),
          patch("resona_postprocess.sources.build_pipeline_from_config",
                return_value=_noop_pipeline()),
      ):
          result = runner.invoke(app, ["transcribe", str(tmp_path),
                                        "--output-dir", str(out_dir)])

      assert result.exit_code == 0
      mock_engine.transcribe.assert_called_once()
      assert (out_dir / "audio.txt").read_text() == "hi"
  ```

- [ ] **Step 3: Run new tests to verify they fail**

  Run: `uv run pytest apps/resona-cli/tests/test_transcribe.py -v --tb=short 2>&1 | head -40`
  Expected: multiple FAIL — the new gateway tests will fail because `transcribe.py` still uses old routing.

- [ ] **Step 4: Rewrite `transcribe.py`**

  Replace the entire file with:

  ```python
  import glob as _glob
  from pathlib import Path
  from typing import Optional
  import typer
  import httpx

  from .local_engine import LocalEngine
  from .engine import InProcessEngine
  from resona_client.client import ResonaClient
  from resona_client.config import EngineConfig
  from .engines import BUILTIN_ENGINES

  EXTENSIONS = {"wav", "webm", "flac", "mp3", "m4a", "ogg", "aac"}


  def _expand_inputs(inputs: list[str], recursive: bool) -> list[Path]:
      """Expand file paths, glob patterns, and/or directories into audio files."""
      out: list[Path] = []
      seen: set[Path] = set()

      def _add(p: Path) -> None:
          rp = p.resolve()
          if rp in seen:
              return
          seen.add(rp)
          out.append(p)

      for raw in inputs:
          if any(ch in raw for ch in "*?["):
              matches = [Path(m) for m in _glob.glob(raw, recursive=recursive)]
              for m in matches:
                  if m.is_file() and m.suffix.lstrip(".").lower() in EXTENSIONS:
                      _add(m)
              continue

          p = Path(raw)
          if p.is_dir():
              glob_fn = p.rglob if recursive else p.glob
              for ext in EXTENSIONS:
                  for f in glob_fn(f"*.{ext}"):
                      _add(f)
          elif p.is_file():
              _add(p)
          else:
              typer.echo(f"Not found: {raw}", err=True)

      return out


  def transcribe_files(
      inputs: list[str] = typer.Argument(
          ..., help="Audio files, glob patterns, or directories.", metavar="INPUTS..."),
      recursive: bool = typer.Option(False, "--recursive", "-r",
          help="Recurse into directories / use `**` in glob patterns."),
      output_dir: Optional[Path] = typer.Option(None, "--output-dir",
          help="Directory to write transcripts."),
      model: Optional[str] = typer.Option(None, "--model",
          help="Model name forwarded to the gateway engine."),
      language: str = typer.Option("de", "--language",
          help="Language hint for transcription."),
      engine_timeout: float = typer.Option(120.0, "--engine-timeout",
          help="Seconds to wait for local engine startup (local fallback only)."),
      engine: Optional[str] = typer.Option(None, "--engine",
          help="Engine name forwarded to the gateway, or a built-in local engine for fallback."),
      private: Optional[bool] = typer.Option(None, "--private/--no-private",
          help="Require a private engine (forwarded to gateway)."),
  ):
      """Transcribe audio files. Uses the gateway by default; falls back to a local engine."""
      files = _expand_inputs(inputs, recursive=recursive)
      if not files:
          print("No audio files found.")
          return

      cfg = EngineConfig.load()
      want_private = cfg.default_private if private is None else private

      try:
          client = ResonaClient.from_config(auto_start=False)
          _transcribe_via_gateway(client, files, output_dir, model, language,
                                   engine, want_private)
          return
      except (httpx.ConnectError, httpx.TimeoutException, RuntimeError):
          typer.echo("No server reachable — running engine locally.", err=True)

      local_engine_name = engine if engine in BUILTIN_ENGINES else cfg.default_engine
      _transcribe_local_fallback(files, output_dir, model, language,
                                  engine_timeout, local_engine_name)


  def _transcribe_via_gateway(
      client: ResonaClient,
      files: list[Path],
      output_dir: Optional[Path],
      model: Optional[str],
      language: str,
      engine: Optional[str],
      private: bool,
  ) -> None:
      if output_dir:
          output_dir.mkdir(parents=True, exist_ok=True)
      for filepath in files:
          try:
              kwargs: dict = {"language": language, "private": private}
              if model:
                  kwargs["model"] = model
              if engine:
                  kwargs["engine"] = engine
              result = client.create_transcription(filepath, **kwargs)
              transcript = result.get("text", "")
              out_path = (output_dir or filepath.parent) / f"{filepath.stem}.txt"
              out_path.write_text(transcript, encoding="utf-8")
              print(f"Transcribed {filepath.name} -> {out_path}")
          except httpx.HTTPStatusError as e:
              typer.echo(f"Failed to transcribe {filepath.name}: {e}", err=True)


  def _transcribe_local_fallback(
      files: list[Path],
      output_dir: Optional[Path],
      model: Optional[str],
      language: str,
      engine_timeout: float,
      engine: str = "faster-whisper",
  ) -> None:
      from resona_postprocess.sources import build_pipeline_from_config

      if not files:
          print("No audio files found.")
          return

      local_engine, cleanup = _resolve_local_engine(model, engine_timeout, engine)
      pipeline = build_pipeline_from_config()

      if output_dir:
          output_dir.mkdir(parents=True, exist_ok=True)

      try:
          for filepath in files:
              try:
                  result = local_engine.transcribe(filepath, language=language)
                  raw_text = result.get("text", "")
                  transcript = pipeline.run(raw_text)
                  out_path = (output_dir or filepath.parent) / f"{filepath.stem}.txt"
                  out_path.write_text(transcript, encoding="utf-8")
                  print(f"Transcribed {filepath.name} -> {out_path}")
              except (httpx.HTTPStatusError, httpx.RequestError) as e:
                  typer.echo(f"Failed to transcribe {filepath.name}: {e}", err=True)
      finally:
          cleanup()


  def _resolve_local_engine(model, engine_timeout, engine):
      try:
          engine_obj = InProcessEngine(engine=engine)
          typer.echo(
              f"No server reachable — running engine '{engine}' in-process.",
              err=True,
          )
          return engine_obj, (lambda: None)
      except ImportError:
          typer.echo(
              f"No server reachable — starting local engine subprocess (engine={engine}).",
              err=True,
          )
          ctx = LocalEngine(model=model, timeout=engine_timeout, engine=engine)
          engine_obj = ctx.__enter__()
          return engine_obj, (lambda: ctx.__exit__(None, None, None))
  ```

- [ ] **Step 5: Run tests to verify they pass**

  Run: `uv run pytest apps/resona-cli/tests/test_transcribe.py -v`
  Expected: all PASS.

- [ ] **Step 6: Commit**

  ```bash
  git add apps/resona-cli/src/resona_cli/transcribe.py apps/resona-cli/tests/test_transcribe.py
  git commit -m "feat(cli): resona transcribe uses sync gateway by default, local fallback"
  ```

---

## Task 3: `resona submit` command

**Files:**
- Create: `apps/resona-cli/src/resona_cli/submit.py`
- Modify: `apps/resona-cli/src/resona_cli/main.py`
- Create: `apps/resona-cli/tests/test_submit.py`

- [ ] **Step 1: Write the failing tests**

  Create `apps/resona-cli/tests/test_submit.py`:

  ```python
  """Tests for resona_cli.submit.submit_files."""
  import io
  import struct
  import wave
  from pathlib import Path
  from unittest.mock import MagicMock, patch

  import pytest
  from typer.testing import CliRunner

  runner = CliRunner()


  def make_wav(path: Path) -> Path:
      buf = io.BytesIO()
      with wave.open(buf, "wb") as w:
          w.setnchannels(1)
          w.setsampwidth(2)
          w.setframerate(16000)
          w.writeframes(struct.pack("<" + "h" * 160, *([0] * 160)))
      path.write_bytes(buf.getvalue())
      return path


  def _make_client(job_id=42):
      c = MagicMock()
      c.base_url = "http://localhost:7000"
      c.submit_job.return_value = {"id": job_id}
      return c


  def test_submit_prints_url(tmp_path):
      from resona_cli.main import app
      make_wav(tmp_path / "a.wav")
      mock_client = _make_client(job_id=99)

      with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
          result = runner.invoke(app, ["submit", str(tmp_path / "a.wav")])

      assert "http://localhost:7000/job/99" in result.output
      assert result.exit_code == 0


  def test_submit_prints_one_url_per_file(tmp_path):
      from resona_cli.main import app
      make_wav(tmp_path / "a.wav")
      make_wav(tmp_path / "b.wav")
      mock_client = MagicMock()
      mock_client.base_url = "http://localhost:7000"
      mock_client.submit_job.side_effect = [{"id": 1}, {"id": 2}]

      with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
          result = runner.invoke(app, ["submit", str(tmp_path / "a.wav"),
                                        str(tmp_path / "b.wav")])

      assert "http://localhost:7000/job/1" in result.output
      assert "http://localhost:7000/job/2" in result.output


  def test_submit_forwards_engine(tmp_path):
      from resona_cli.main import app
      make_wav(tmp_path / "a.wav")
      mock_client = _make_client()

      with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
          runner.invoke(app, ["submit", str(tmp_path / "a.wav"), "--engine", "deepgram"])

      call_kwargs = mock_client.submit_job.call_args.kwargs
      assert call_kwargs.get("engine") == "deepgram"


  def test_submit_forwards_translate(tmp_path):
      from resona_cli.main import app
      make_wav(tmp_path / "a.wav")
      mock_client = _make_client()

      with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
          runner.invoke(app, ["submit", str(tmp_path / "a.wav"), "--translate"])

      call_kwargs = mock_client.submit_job.call_args.kwargs
      assert call_kwargs.get("translate") is True


  def test_submit_no_server_exits_with_error(tmp_path):
      from resona_cli.main import app
      make_wav(tmp_path / "a.wav")

      with patch("resona_client.client.ResonaClient.from_config",
                  side_effect=RuntimeError("no server")):
          result = runner.invoke(app, ["submit", str(tmp_path / "a.wav")])

      assert result.exit_code != 0


  def test_submit_no_files_exits(tmp_path):
      from resona_cli.main import app
      with patch("resona_client.client.ResonaClient.from_config", return_value=_make_client()):
          result = runner.invoke(app, ["submit", str(tmp_path)])
      assert result.exit_code != 0
  ```

- [ ] **Step 2: Run tests to verify they fail**

  Run: `uv run pytest apps/resona-cli/tests/test_submit.py -v --tb=short 2>&1 | head -20`
  Expected: FAIL — `No such command 'submit'`

- [ ] **Step 3: Create `submit.py`**

  Create `apps/resona-cli/src/resona_cli/submit.py`:

  ```python
  """resona submit — send audio to the async job queue and print result URLs."""
  from pathlib import Path
  from typing import Optional

  import typer

  from resona_client.client import ResonaClient
  from .transcribe import _expand_inputs


  def submit_files(
      inputs: list[str] = typer.Argument(
          ..., help="Audio files, glob patterns, or directories.", metavar="FILES..."),
      engine: Optional[str] = typer.Option(None, "--engine",
          help="Engine name to forward to the gateway."),
      language: str = typer.Option("de", "--language",
          help="Language hint (informational; stored on the job)."),
      translate: bool = typer.Option(False, "--translate",
          help="Request English translation instead of transcription."),
  ):
      """Submit audio to the async job queue. Prints one result URL per file immediately."""
      files = _expand_inputs(inputs, recursive=False)
      if not files:
          typer.echo("No audio files found.", err=True)
          raise typer.Exit(1)

      try:
          client = ResonaClient.from_config(auto_start=False)
      except RuntimeError as e:
          typer.echo(f"Error: {e}", err=True)
          raise typer.Exit(1)

      for filepath in files:
          try:
              job = client.submit_job(filepath, translate=translate, engine=engine)
              job_id = job["id"]
              typer.echo(f"{client.base_url}/job/{job_id}")
          except Exception as e:
              typer.echo(f"Error submitting {filepath.name}: {e}", err=True)
  ```

- [ ] **Step 4: Register in `main.py`**

  In `apps/resona-cli/src/resona_cli/main.py`, add after the `transcribe` import and command registration:

  ```python
  from .submit import submit_files
  ```

  And after `app.command("transcribe")(transcribe_files)`:

  ```python
  app.command("submit")(submit_files)
  ```

- [ ] **Step 5: Run tests to verify they pass**

  Run: `uv run pytest apps/resona-cli/tests/test_submit.py -v`
  Expected: all PASS.

- [ ] **Step 6: Commit**

  ```bash
  git add apps/resona-cli/src/resona_cli/submit.py \
          apps/resona-cli/src/resona_cli/main.py \
          apps/resona-cli/tests/test_submit.py
  git commit -m "feat(cli): resona submit command — async queue, returns job URL"
  ```

---

## Task 4: `resona speech` command

**Files:**
- Create: `apps/resona-cli/src/resona_cli/speech.py`
- Modify: `apps/resona-cli/src/resona_cli/main.py`
- Create: `apps/resona-cli/tests/test_speech.py`

- [ ] **Step 1: Write the failing tests**

  Create `apps/resona-cli/tests/test_speech.py`:

  ```python
  """Tests for resona_cli.speech.speak command."""
  import shutil
  import subprocess
  from pathlib import Path
  from unittest.mock import MagicMock, patch

  import httpx
  import pytest
  from typer.testing import CliRunner

  runner = CliRunner()
  FAKE_AUDIO = b"\xff\xfb\x90\x00" * 100


  def _make_speech_client(audio=FAKE_AUDIO):
      c = MagicMock()
      c.create_speech.return_value = audio
      return c


  def test_speech_writes_default_output(tmp_path, monkeypatch):
      from resona_cli.main import app
      monkeypatch.chdir(tmp_path)
      mock_client = _make_speech_client()

      with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
          result = runner.invoke(app, ["speech", "hello world"])

      assert result.exit_code == 0
      assert (tmp_path / "speech.mp3").exists()
      assert (tmp_path / "speech.mp3").read_bytes() == FAKE_AUDIO


  def test_speech_writes_to_custom_output(tmp_path):
      from resona_cli.main import app
      out = tmp_path / "out.mp3"
      mock_client = _make_speech_client()

      with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
          result = runner.invoke(app, ["speech", "hello", "--output", str(out)])

      assert result.exit_code == 0
      assert out.read_bytes() == FAKE_AUDIO


  def test_speech_forwards_voice_engine_model(tmp_path, monkeypatch):
      from resona_cli.main import app
      monkeypatch.chdir(tmp_path)
      mock_client = _make_speech_client()

      with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
          runner.invoke(app, ["speech", "hi",
                               "--voice", "nova",
                               "--engine", "openai",
                               "--model", "tts-1-hd",
                               "--format", "opus",
                               "--speed", "1.2"])

      call_kwargs = mock_client.create_speech.call_args.kwargs
      assert call_kwargs.get("voice") == "nova"
      assert call_kwargs.get("engine") == "openai"
      assert call_kwargs.get("model") == "tts-1-hd"
      assert call_kwargs.get("response_format") == "opus"
      assert abs(call_kwargs.get("speed", 0) - 1.2) < 0.001


  def test_speech_http_error_exits_nonzero(tmp_path, monkeypatch):
      from resona_cli.main import app
      monkeypatch.chdir(tmp_path)
      mock_client = MagicMock()
      req = MagicMock()
      resp = MagicMock()
      resp.status_code = 400
      resp.text = "bad engine"
      mock_client.create_speech.side_effect = httpx.HTTPStatusError(
          "bad", request=req, response=resp
      )

      with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
          result = runner.invoke(app, ["speech", "hi"])

      assert result.exit_code != 0


  def test_speech_no_server_exits_nonzero():
      from resona_cli.main import app
      with patch("resona_client.client.ResonaClient.from_config",
                  side_effect=RuntimeError("no server")):
          result = runner.invoke(app, ["speech", "hi"])
      assert result.exit_code != 0


  def test_speech_play_flag_calls_player(tmp_path, monkeypatch):
      from resona_cli.main import app
      monkeypatch.chdir(tmp_path)
      mock_client = _make_speech_client()

      with (
          patch("resona_client.client.ResonaClient.from_config", return_value=mock_client),
          patch("shutil.which", return_value="/usr/bin/mpv"),
          patch("subprocess.run") as mock_run,
      ):
          result = runner.invoke(app, ["speech", "hi", "--play"])

      assert result.exit_code == 0
      mock_run.assert_called_once()
      cmd = mock_run.call_args.args[0]
      assert cmd[0] == "mpv"


  def test_speech_play_no_player_warns(tmp_path, monkeypatch):
      from resona_cli.main import app
      monkeypatch.chdir(tmp_path)
      mock_client = _make_speech_client()

      with (
          patch("resona_client.client.ResonaClient.from_config", return_value=mock_client),
          patch("shutil.which", return_value=None),
      ):
          result = runner.invoke(app, ["speech", "hi", "--play"])

      assert result.exit_code == 0
      assert "no audio player" in result.output.lower() or "no audio player" in (result.stderr or "").lower()
  ```

- [ ] **Step 2: Run tests to verify they fail**

  Run: `uv run pytest apps/resona-cli/tests/test_speech.py -v --tb=short 2>&1 | head -20`
  Expected: FAIL — `No such command 'speech'`

- [ ] **Step 3: Create `speech.py`**

  Create `apps/resona-cli/src/resona_cli/speech.py`:

  ```python
  """resona speech — synthesise speech from text via the gateway."""
  import shutil
  import subprocess
  import sys
  import tempfile
  from pathlib import Path
  from typing import Optional

  import typer

  from resona_client.client import ResonaClient


  def speak(
      text: str = typer.Argument(..., help="Text to synthesise."),
      output: Optional[str] = typer.Option(None, "--output",
          help="Output file path. Use '-' for stdout. Defaults to speech.mp3 in cwd."),
      engine: Optional[str] = typer.Option(None, "--engine",
          help="Engine name forwarded to the gateway."),
      voice: str = typer.Option("alloy", "--voice", help="Voice name."),
      model: str = typer.Option("tts-1", "--model", help="TTS model name."),
      fmt: str = typer.Option("mp3", "--format",
          help="Output format: mp3, opus, aac, flac."),
      speed: float = typer.Option(1.0, "--speed", help="Speech speed (0.25–4.0)."),
      private: bool = typer.Option(False, "--private",
          help="Require a private engine."),
      play: bool = typer.Option(False, "--play",
          help="Pipe audio to a local player (mpv, ffplay, afplay) instead of saving."),
  ):
      """Synthesise speech from TEXT via the gateway TTS engine."""
      try:
          client = ResonaClient.from_config(auto_start=False)
      except RuntimeError as e:
          typer.echo(f"Error: {e}", err=True)
          raise typer.Exit(1)

      try:
          kwargs: dict = {
              "voice": voice,
              "model": model,
              "response_format": fmt,
              "speed": speed,
              "private": private,
          }
          if engine:
              kwargs["engine"] = engine
          audio = client.create_speech(text, **kwargs)
      except Exception as e:
          typer.echo(f"Error: {e}", err=True)
          raise typer.Exit(1)

      if play:
          _play_audio(audio, fmt)
          return

      if output == "-":
          sys.stdout.buffer.write(audio)
          return

      out_path = Path(output) if output else Path("speech.mp3")
      out_path.write_bytes(audio)
      typer.echo(f"Saved to {out_path}")


  def _play_audio(data: bytes, fmt: str) -> None:
      """Pipe audio bytes to the first available player."""
      for cmd in (
          ["mpv", "--no-video", "--really-quiet", "-"],
          ["ffplay", "-nodisp", "-autoexit", "-i", "pipe:0"],
      ):
          if shutil.which(cmd[0]):
              subprocess.run(cmd, input=data, check=False)
              return
      if shutil.which("afplay"):
          with tempfile.NamedTemporaryFile(suffix=f".{fmt}", delete=False) as f:
              f.write(data)
              tmp = f.name
          try:
              subprocess.run(["afplay", tmp], check=False)
          finally:
              import os
              os.unlink(tmp)
          return
      typer.echo("Warning: no audio player found (tried mpv, ffplay, afplay)", err=True)
  ```

- [ ] **Step 4: Register in `main.py`**

  In `apps/resona-cli/src/resona_cli/main.py`, add:

  ```python
  from .speech import speak
  ```

  And after `app.command("submit")(submit_files)`:

  ```python
  app.command("speech")(speak)
  ```

- [ ] **Step 5: Run tests to verify they pass**

  Run: `uv run pytest apps/resona-cli/tests/test_speech.py -v`
  Expected: all PASS.

- [ ] **Step 6: Commit**

  ```bash
  git add apps/resona-cli/src/resona_cli/speech.py \
          apps/resona-cli/src/resona_cli/main.py \
          apps/resona-cli/tests/test_speech.py
  git commit -m "feat(cli): resona speech command — TTS with --play, --voice, --format, --engine"
  ```

---

## Task 5: `resona engines status` subcommand

**Files:**
- Modify: `apps/resona-cli/src/resona_cli/engines.py`
- Modify: `apps/resona-cli/tests/test_engines.py`

- [ ] **Step 1: Write the failing test**

  Append to `apps/resona-cli/tests/test_engines.py`:

  ```python
  # ── engines status ────────────────────────────────────────────────────────────

  def test_engines_status_shows_catalogue(isolated_config):
      catalogue = {
          "engines": [
              {"name": "faster-whisper", "kind": "local", "capabilities": ["stt"],
               "private": True, "available": True, "models": ["large-v3"],
               "url": "http://localhost:7001", "provider": None},
              {"name": "openai", "kind": "cloud", "capabilities": ["stt", "tts"],
               "private": False, "available": True, "models": ["whisper-1", "tts-1"],
               "url": None, "provider": "openai"},
          ],
          "default": "faster-whisper",
      }
      mock_client = MagicMock()
      mock_client.list_engines.return_value = catalogue

      with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
          result = runner.invoke(app, ["engines", "status"])

      assert result.exit_code == 0
      assert "faster-whisper" in result.output
      assert "openai" in result.output


  def test_engines_status_no_server_exits_nonzero(isolated_config):
      with patch("resona_client.client.ResonaClient.from_config",
                  side_effect=RuntimeError("no server")):
          result = runner.invoke(app, ["engines", "status"])
      assert result.exit_code != 0
  ```

  You also need to add to the imports at the top of `test_engines.py`:

  ```python
  from unittest.mock import MagicMock, patch
  ```

  Check the existing imports — if `MagicMock` and `patch` are already imported, skip adding them.

- [ ] **Step 2: Run the tests to verify they fail**

  Run: `uv run pytest apps/resona-cli/tests/test_engines.py::test_engines_status_shows_catalogue apps/resona-cli/tests/test_engines.py::test_engines_status_no_server_exits_nonzero -v --tb=short`
  Expected: FAIL — `No such command 'status'`

- [ ] **Step 3: Add `status` subcommand to `engines.py`**

  Append to `apps/resona-cli/src/resona_cli/engines.py`:

  ```python
  @engines_app.command("status")
  def engines_status():
      """Show the live gateway catalogue of available engines and their status."""
      from resona_client.client import ResonaClient

      try:
          client = ResonaClient.from_config(auto_start=False)
          data = client.list_engines()
      except RuntimeError as e:
          typer.echo(f"Error: {e}", err=True)
          raise typer.Exit(1)
      except Exception as e:
          typer.echo(f"Error reaching gateway: {e}", err=True)
          raise typer.Exit(1)

      engines = data.get("engines", [])
      default_name = data.get("default")

      if not engines:
          typer.echo("No engines in catalogue.")
          return

      try:
          from rich.table import Table
          from rich.console import Console

          table = Table(title="Engine Catalogue")
          table.add_column("Name")
          table.add_column("Kind")
          table.add_column("Capabilities")
          table.add_column("Available")
          table.add_column("Models")
          for e in engines:
              name = e["name"]
              if name == default_name:
                  name = f"[bold]{name}[/bold] (default)"
              avail = "[green]✓[/green]" if e.get("available") else "[red]✗[/red]"
              caps = ", ".join(e.get("capabilities", []))
              models = ", ".join(e.get("models", [])) or "-"
              table.add_row(name, e.get("kind", ""), caps, avail, models)
          Console().print(table)
      except ImportError:
          typer.echo(f"  {'NAME':<22}{'KIND':<9}{'CAPS':<12}{'AVAIL':<8}MODELS")
          for e in engines:
              name = e["name"]
              if name == default_name:
                  name += " (default)"
              avail = "✓" if e.get("available") else "✗"
              caps = ",".join(e.get("capabilities", []))
              models = ",".join(e.get("models", [])) or "-"
              typer.echo(f"  {name:<22}{e.get('kind', ''):<9}{caps:<12}{avail:<8}{models}")
  ```

- [ ] **Step 4: Run the tests to verify they pass**

  Run: `uv run pytest apps/resona-cli/tests/test_engines.py -v`
  Expected: all PASS.

- [ ] **Step 5: Commit**

  ```bash
  git add apps/resona-cli/src/resona_cli/engines.py apps/resona-cli/tests/test_engines.py
  git commit -m "feat(cli): resona engines status — live gateway catalogue"
  ```

---

## Task 6: Justfile additions

**Files:**
- Modify: `justfile`

- [ ] **Step 1: Add targets to `justfile`**

  After the `rebuild` recipe (in the `# ── Docker` section), add per-profile shortcuts:

  ```just
  # Start only the faster-whisper engine + API
  up-faster-whisper:
      docker compose -f docker-compose.resona.yml --profile faster-whisper up -d

  # Start only the whisper (PyTorch) engine + API
  up-whisper:
      docker compose -f docker-compose.resona.yml --profile whisper up -d

  # Start only the voxtral engine + API
  up-voxtral:
      docker compose -f docker-compose.resona.yml --profile voxtral up -d
  ```

  After `docs-build`, add:

  ```just
  # ── Code quality ──────────────────────────────────────────────────────

  # Auto-format all Python files and apply safe lint fixes
  format:
      uv run ruff format .
      uv run ruff check --fix .

  # Check for lint issues without modifying files
  lint:
      uv run ruff check .
  ```

- [ ] **Step 2: Verify the justfile is valid**

  Run: `just --list`
  Expected: clean output listing all recipes including `up-faster-whisper`, `up-whisper`, `up-voxtral`, `format`, `lint`.

- [ ] **Step 3: Commit**

  ```bash
  git add justfile
  git commit -m "chore(justfile): per-profile docker targets, format, lint"
  ```

---

## Task 7: Full verification

**Files:** none — verification only

- [ ] **Step 1: Run the full test suite**

  Run: `uv run pytest --tb=short -q`
  Expected: all PASS, 0 failures. Note the total count.

- [ ] **Step 2: Verify CLI commands are registered**

  Run: `uv run resona --help`
  Expected: output lists `transcribe`, `submit`, `speech`, `engines`, `watch`, `rec`, `live`, `ui`.

  Run: `uv run resona engines --help`
  Expected: output lists `list`, `add`, `remove`, `test`, `status`.

- [ ] **Step 3: Smoke-check imports**

  Run: `uv run python -c "from resona_cli.submit import submit_files; from resona_cli.speech import speak; print('OK')"`
  Expected: `OK`

- [ ] **Step 4: Commit if anything was fixed in this step, then report done**
