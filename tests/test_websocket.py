from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import pytest
import sys
from pathlib import Path

# Add src to path for testing
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ws_server.api.app import app

client = TestClient(app)

@pytest.fixture
def mock_transcriber_factory():
    """Mock the getTranscriber factory."""
    with patch("ws_server.api.ws_transcribe.getTranscriber") as mock_factory:
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = {
            "text": "Hello world this is a test transcript",
            "segments": [],
            "language": "de"
        }
        mock_factory.return_value = mock_transcriber
        yield mock_transcriber

def test_websocket_connection(mock_transcriber_factory):
    """Test that we can connect to the websocket endpoint."""
    with client.websocket_connect("/ws/transcribe") as websocket:
        # Just connecting and closing should be fine
        pass

def test_websocket_audio_flow(mock_transcriber_factory):
    """Test the full audio flow: send audio -> get transcript."""
    with client.websocket_connect("/ws/transcribe") as websocket:
        # Simulate sending a chunk of audio
        # We need to send valid JSON structure as expected by the server
        # The server expects {"type": "audio", "data": "base64...", "sample_rate": 16000}
        
        # Create dummy audio data (silence)
        # 16000 Hz * 2 seconds * 2 bytes/sample (int16)
        import base64
        import numpy as np
        
        # Create 2 seconds of silence (zeros) in int16 format
        audio_data = np.zeros(32000, dtype=np.int16)
        audio_bytes = audio_data.tobytes()
        audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
        
        payload = {
            "type": "audio",
            "data": audio_b64,
            "sample_rate": 16000
        }
        
        websocket.send_json(payload)
        
        # We expect a transcript response
        # Note: The server buffers audio, so we might need to send enough data to trigger a chunk
        # In ws_transcribe.py: CHUNK_DURATION = 2.0, min_chunk_size = 1.0
        # We sent 2.0 seconds, so it should trigger.
        
        data = websocket.receive_json()
        assert data["type"] == "transcript"
        assert data["text"] == "Hello world this is a test transcript"
        assert data["is_final"] is False

def test_websocket_stop_flow(mock_transcriber_factory):
    """Test the stop flow: send audio -> stop -> get final transcript."""
    with client.websocket_connect("/ws/transcribe") as websocket:
        # Send some audio
        # We send LESS than 1.0 second (min_chunk_size) to ensure it stays in the buffer
        # and is processed only when "stop" is received.
        import base64
        import numpy as np
        
        audio_data = np.zeros(8000, dtype=np.int16) # 0.5 second
        audio_bytes = audio_data.tobytes()
        audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
        
        payload = {
            "type": "audio",
            "data": audio_b64,
            "sample_rate": 16000
        }
        websocket.send_json(payload)
        
        # Send stop command
        websocket.send_json({"type": "stop"})
        
        # The server processes remaining audio on stop.
        # It might send an intermediate transcript if buffer was full enough (1.0s is min_chunk_size)
        # OR it waits for the final result.
        
        # We expect one or more messages. The last one should be "stopped".
        messages = []
        while True:
            data = websocket.receive_json()
            messages.append(data)
            if data["type"] == "stopped":
                break
        
        # Check that we received at least one transcript and the stopped message
        has_transcript = any(m["type"] == "transcript" for m in messages)
        has_stopped = any(m["type"] == "stopped" for m in messages)
        
        assert has_transcript
        assert has_stopped
        
        # Check final transcript flag
        final_transcripts = [m for m in messages if m.get("type") == "transcript" and m.get("is_final") is True]
        assert len(final_transcripts) > 0

def test_websocket_invalid_message(mock_transcriber_factory):
    """Test handling of invalid messages."""
    with client.websocket_connect("/ws/transcribe") as websocket:
        websocket.send_text("This is not JSON")
        data = websocket.receive_json()
        assert data["type"] == "error"
        assert "Invalid JSON" in data["message"]

def test_websocket_unknown_type(mock_transcriber_factory):
    """Test handling of unknown message types."""
    with client.websocket_connect("/ws/transcribe") as websocket:
        websocket.send_json({"type": "unknown_type"})
        # Server simply logs warning for unknown types, doesn't send error back currently based on read code.
        # Let's check if it breaks by sending a valid message afterwards
        
        import base64
        import numpy as np
        audio_data = np.zeros(32000, dtype=np.int16)
        audio_bytes = audio_data.tobytes()
        audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
        
        websocket.send_json({
            "type": "audio",
            "data": audio_b64,
            "sample_rate": 16000
        })
        
        data = websocket.receive_json()
        assert data["type"] == "transcript"
