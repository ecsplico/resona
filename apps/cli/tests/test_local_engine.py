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
