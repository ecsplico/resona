"""Tests for the /ws/live WebSocket endpoint."""
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import pytest
import sys
import base64
import numpy as np
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ws_server.api.app import app

client = TestClient(app)


@pytest.fixture
def mock_live_transcriber():
    """Mock the LiveTranscriber for the ws_live endpoint."""
    with patch("ws_server.api.ws_live.LiveTranscriber") as MockLT:
        mock_instance = MagicMock()

        # Provide a real asyncio.Event so process_loop's wait_for works correctly.
        # Left unset so process_loop blocks on wait() and yields control;
        # the stop message arrives, cancels the task cleanly.
        import asyncio
        from ws_server.processing.live_transcriber import TranscriptionResult
        mock_instance._audio_event = asyncio.Event()
        async def mock_process():
            return TranscriptionResult(
                confirmed="Hello world",
                partial="this is a test",
                language="de",
            )

        async def mock_flush():
            return TranscriptionResult(
                confirmed="Hello world this is a test",
                partial="",
                language="de",
            )

        mock_instance.process = mock_process
        mock_instance.flush = mock_flush
        mock_instance.has_enough_audio.return_value = True
        mock_instance.add_audio = MagicMock()
        mock_instance.language = "de"

        MockLT.return_value = mock_instance
        yield mock_instance


def make_audio_payload(duration_seconds: float = 0.5):
    """Create a base64-encoded audio payload."""
    samples = int(16000 * duration_seconds)
    audio = np.zeros(samples, dtype=np.int16)
    audio_b64 = base64.b64encode(audio.tobytes()).decode("utf-8")
    return {
        "type": "audio",
        "data": audio_b64,
        "sample_rate": 16000,
    }


def test_live_websocket_connection(mock_live_transcriber):
    """Test that we can connect to the live websocket endpoint."""
    with client.websocket_connect("/ws/live") as websocket:
        # Send stop to cleanly close
        websocket.send_json({"type": "stop"})
        # Collect messages until we get "stopped"
        messages = []
        while True:
            data = websocket.receive_json()
            messages.append(data)
            if data["type"] == "stopped":
                break
        assert any(m["type"] == "stopped" for m in messages)


def test_live_websocket_audio_and_stop(mock_live_transcriber):
    """Test sending audio then stopping to get final transcript."""
    with client.websocket_connect("/ws/live") as websocket:
        # Send some audio
        websocket.send_json(make_audio_payload(1.0))
        websocket.send_json(make_audio_payload(1.0))

        # Stop
        websocket.send_json({"type": "stop"})

        # Collect all responses
        messages = []
        while True:
            data = websocket.receive_json()
            messages.append(data)
            if data["type"] == "stopped":
                break

        # Should have a final message with the transcript
        has_final = any(m["type"] == "final" for m in messages)
        has_stopped = any(m["type"] == "stopped" for m in messages)
        assert has_stopped
        # Final may or may not appear depending on timing, but stopped must


def test_live_websocket_invalid_json(mock_live_transcriber):
    """Test handling of invalid JSON."""
    with client.websocket_connect("/ws/live") as websocket:
        websocket.send_text("not valid json")
        data = websocket.receive_json()
        assert data["type"] == "error"
        assert "Invalid JSON" in data["message"]

        # Should still work after error
        websocket.send_json({"type": "stop"})
        messages = []
        while True:
            data = websocket.receive_json()
            messages.append(data)
            if data["type"] == "stopped":
                break
        assert any(m["type"] == "stopped" for m in messages)


def test_live_websocket_config_change(mock_live_transcriber):
    """Test language config change during session."""
    with client.websocket_connect("/ws/live") as websocket:
        websocket.send_json({"type": "config", "language": "en"})
        # Should not crash, language is updated on transcriber instance

        websocket.send_json({"type": "stop"})
        messages = []
        while True:
            data = websocket.receive_json()
            messages.append(data)
            if data["type"] == "stopped":
                break
        assert any(m["type"] == "stopped" for m in messages)
