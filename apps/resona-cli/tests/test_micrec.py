"""Tests for resona_cli.micrec — RecordingSession and MicRecApp (no real audio hardware)."""
import queue
import threading
from unittest.mock import MagicMock, patch

import pytest

# These tests need sounddevice/soundfile/textual — base resona-cli dependencies.
pytest.importorskip("sounddevice", reason="sounddevice not installed; reinstall resona-cli")

import numpy as np
from resona_cli.micrec import RecordingSession, MicRecApp


# ── RecordingSession ──────────────────────────────────────────────────────────

def test_recording_session_initial_state(tmp_path):
    session = RecordingSession(filename=str(tmp_path / "out.wav"))
    assert not session.stop_event.is_set()
    assert not session.pause_event.is_set()
    assert not session.save_finished_event.is_set()
    assert session.audio_queue.empty()
    assert session.thread is None


def test_add_and_remove_audio_observer():
    session = RecordingSession(filename="/tmp/test.wav")
    callback = MagicMock()
    session.add_audio_observer(callback)
    assert callback in session._audio_observers
    session.remove_audio_observer(callback)
    assert callback not in session._audio_observers


def test_remove_nonexistent_observer_noop():
    session = RecordingSession(filename="/tmp/test.wav")
    session.remove_audio_observer(MagicMock())  # should not raise


def test_stop_sets_event():
    session = RecordingSession(filename="/tmp/test.wav")
    session.stop()
    assert session.stop_event.is_set()


def test_drain_queue_clears_audio():
    session = RecordingSession(filename="/tmp/test.wav")
    session.audio_queue.put(np.zeros(10))
    session.audio_queue.put(np.zeros(10))
    session.drain_queue()
    assert session.audio_queue.empty()


def test_reset_events():
    session = RecordingSession(filename="/tmp/test.wav")
    session.stop_event.set()
    session.pause_event.set()
    session.save_finished_event.set()
    session.reset_events()
    assert not session.stop_event.is_set()
    assert not session.pause_event.is_set()
    assert not session.save_finished_event.is_set()


def test_audio_callback_notifies_observers():
    """_audio_callback calls observers with a copy of each chunk."""
    session = RecordingSession(filename="/tmp/test.wav")
    received = []
    session.add_audio_observer(lambda chunk: received.append(chunk))

    mock_app = MagicMock()
    mock_app.is_recording = True
    # pause_event not set → recording active
    chunk = np.ones((16, 1), dtype="float32")
    session._audio_callback(chunk, 16, None, None, mock_app)

    assert len(received) == 1
    assert np.array_equal(received[0], chunk)


def test_audio_callback_skips_when_paused():
    session = RecordingSession(filename="/tmp/test.wav")
    session.pause_event.set()
    received = []
    session.add_audio_observer(lambda chunk: received.append(chunk))

    mock_app = MagicMock()
    mock_app.is_recording = True
    chunk = np.ones((16, 1), dtype="float32")
    session._audio_callback(chunk, 16, None, None, mock_app)

    assert len(received) == 0
    assert session.audio_queue.empty()


def test_audio_callback_observer_exception_does_not_crash():
    session = RecordingSession(filename="/tmp/test.wav")

    def bad_observer(chunk):
        raise RuntimeError("observer error")

    session.add_audio_observer(bad_observer)
    mock_app = MagicMock()
    mock_app.is_recording = True
    # Should not raise
    session._audio_callback(np.zeros((16, 1)), 16, None, None, mock_app)


def test_join_with_no_thread():
    session = RecordingSession(filename="/tmp/test.wav")
    session.join(timeout=0.1)  # should not raise or block


# ── run_mic_rec_app ───────────────────────────────────────────────────────────

def test_run_mic_rec_app_audio_error_exits(tmp_path, monkeypatch):
    import sys
    monkeypatch.setenv("FILE_PATH", str(tmp_path))
    with (
        patch("resona_cli.micrec.sd.check_input_settings", side_effect=Exception("no mic")),
        pytest.raises(SystemExit),
    ):
        from resona_cli.micrec import run_mic_rec_app
        run_mic_rec_app()
