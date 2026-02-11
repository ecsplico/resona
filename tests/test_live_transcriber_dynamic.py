"""Tests for the LiveTranscriber dynamic buffering logic."""
import pytest
import numpy as np
from unittest.mock import patch, MagicMock
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

@pytest.fixture
def mock_transcriber_factory():
    """Mock the getTranscriber factory."""
    with patch("ws_server.processing.live_transcriber.getTranscriber") as mock_factory:
        yield mock_factory

def make_audio(duration_seconds: float, sample_rate: int = 16000) -> np.ndarray:
    return np.zeros(int(sample_rate * duration_seconds), dtype=np.float32)

class TestDynamicBuffering:
    """Tests for dynamic buffer slicing based on word timestamps."""

    def test_dynamic_slicing_basic(self, mock_transcriber_factory):
        from ws_server.processing.live_transcriber import LiveTranscriber, SAMPLE_RATE
        
        # Setup mock transcriber
        mock_t = MagicMock()
        mock_transcriber_factory.return_value = mock_t
        
        # Mock result: "Hello world"
        # Word 1: "Hello" (0.0 - 0.5s)
        # Word 2: "world" (0.5 - 1.0s)
        mock_result = {
            "text": "Hello world",
            "language": "en",
            "segments": [
                SimpleNamespace(words=[
                    SimpleNamespace(word="Hello", start=0.0, end=0.5),
                    SimpleNamespace(word="world", start=0.5, end=1.0),
                ])
            ]
        }
        mock_t.transcribe.return_value = mock_result

        lt = LiveTranscriber(language="en")
        
        # Initial state
        lt.add_audio(make_audio(2.0)) # 2 seconds of audio
        
        # Pre-set prev_text to "Hello" so "Hello" becomes confirmed
        lt._prev_text = "Hello"
        
        # Run process
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(lt.process())
        
        # Verification
        assert result is not None
        
        # 1. Check local agreement
        # Prev: "Hello"
        # Curr: "Hello world"
        # Confirmed: "Hello"
        # Partial: "world"
        assert result.confirmed == "Hello"
        assert result.partial == "world"
        
        # 2. Check buffer slicing
        # "Hello" ends at 0.5s.
        # We start with 2.0s audio.
        # Should slice 0.5s. Remaining: 1.5s.
        expected_samples = int((2.0 - 0.5) * SAMPLE_RATE)
        assert len(lt.buffer) == expected_samples, f"Buffer expected {expected_samples}, got {len(lt.buffer)}"
        
        # 3. Check _prev_text update
        # Should be "world" (the partial part that remains and matches the new buffer start)
        assert lt._prev_text == "world"

    def test_no_confirmation_no_slicing(self, mock_transcriber_factory):
        from ws_server.processing.live_transcriber import LiveTranscriber
        
        mock_t = MagicMock()
        mock_transcriber_factory.return_value = mock_t
        
        mock_result = {
            "text": "Hello world",
            "language": "en",
            "segments": [
                SimpleNamespace(words=[
                    SimpleNamespace(word="Hello", start=0.0, end=0.5),
                    SimpleNamespace(word="world", start=0.5, end=1.0),
                ])
            ]
        }
        mock_t.transcribe.return_value = mock_result

        lt = LiveTranscriber(language="en")
        lt.add_audio(make_audio(2.0))
        # prev_text empty -> nothing confirmed
        lt._prev_text = "" 
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(lt.process())
        
        assert result.confirmed == ""
        assert result.partial == "Hello world"
        
        # Buffer should NOT be sliced (except maybe for max size, but 2s is < 30s)
        expected_samples = int(2.0 * 16000)
        assert len(lt.buffer) == expected_samples
        
        # _prev_text should be full text
        assert lt._prev_text == "Hello world"

    def test_partial_confirmation_slicing(self, mock_transcriber_factory):
        from ws_server.processing.live_transcriber import LiveTranscriber, SAMPLE_RATE
        
        mock_t = MagicMock()
        mock_transcriber_factory.return_value = mock_t
        
        # Prev: "This is"
        # Curr: "This is a test"
        mock_result = {
            "text": "This is a test",
            "language": "en",
            "segments": [
                SimpleNamespace(words=[
                    SimpleNamespace(word="This", start=0.0, end=0.4),
                    SimpleNamespace(word="is", start=0.4, end=0.8),
                    SimpleNamespace(word="a", start=0.8, end=1.0),
                    SimpleNamespace(word="test", start=1.0, end=1.5),
                ])
            ]
        }
        mock_t.transcribe.return_value = mock_result

        lt = LiveTranscriber(language="en")
        lt.add_audio(make_audio(3.0))
        lt._prev_text = "This is"
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(lt.process())
        
        # Confirmed: "This is"
        # Partial: "a test"
        assert result.confirmed == "This is"
        assert result.partial == "a test"
        
        # Sliced at "is".end = 0.8s
        expected_samples = int((3.0 - 0.8) * SAMPLE_RATE)
        assert abs(len(lt.buffer) - expected_samples) < 100 # Float logic tolerance
        
        # _prev_text = "a test"
        assert lt._prev_text == "a test"

    def test_buffer_overflow_reset(self, mock_transcriber_factory):
        from ws_server.processing.live_transcriber import LiveTranscriber, MAX_BUFFER_SECONDS, MIN_CHUNK_SECONDS, SAMPLE_RATE
        
        mock_t = MagicMock()
        mock_transcriber_factory.return_value = mock_t
        # Return something so process logic runs
        mock_t.transcribe.return_value = {"text": "foo", "segments": []}
        
        lt = LiveTranscriber()
        lt._prev_text = "Some confirmed text"
        
        # Add massive audio
        overflow_len = MAX_BUFFER_SECONDS + 5.0
        lt.add_audio(make_audio(overflow_len))
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(lt.process())
        
        # Buffer should be capped (removed MIN_CHUNK_SECONDS from START)
        # So length should be roughly (overflow_len - MIN_CHUNK_SECONDS) BUT capped by max check?
        # Logic says: if len > max: drop min_chunk.
        # But here we added MAX+5. 
        # So len is MAX+5.
        # Dropped MIN_CHUNK.
        # Remainder = MAX+5 - MIN.
        # Is that valid? The code says:
        # max_samples = int(MAX_BUFFER_SECONDS * SAMPLE_RATE)
        # if len(self.buffer) > max_samples:
        #    drop_samples = int(MIN_CHUNK_SECONDS * SAMPLE_RATE)
        #    self.buffer = self.buffer[drop_samples:]
        
        expected_samples = int(MAX_BUFFER_SECONDS * SAMPLE_RATE)
        assert abs(len(lt.buffer) - expected_samples) < 1000
        
        # _prev_text should be updated to the new partial text ("foo")
        assert lt._prev_text == "foo"
