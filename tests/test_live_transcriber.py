"""Tests for the LiveTranscriber engine."""
import pytest
import numpy as np
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def mock_transcriber():
    """Mock the getTranscriber factory for LiveTranscriber."""
    with patch("ws_server.processing.live_transcriber.getTranscriber") as mock_factory:
        mock_t = MagicMock()
        mock_t.transcribe.return_value = {
            "text": "Hello world this is a test",
            "segments": [],
            "language": "de",
        }
        mock_factory.return_value = mock_t
        yield mock_t


def make_audio(duration_seconds: float, sample_rate: int = 16000) -> np.ndarray:
    """Create dummy audio data (silence)."""
    return np.zeros(int(sample_rate * duration_seconds), dtype=np.float32)


class TestLiveTranscriberBuffer:
    """Tests for buffer management."""

    def test_add_audio_grows_buffer(self, mock_transcriber):
        from ws_server.processing.live_transcriber import LiveTranscriber
        lt = LiveTranscriber()
        assert lt.buffer_duration() == 0.0

        lt.add_audio(make_audio(1.0))
        assert abs(lt.buffer_duration() - 1.0) < 0.01

        lt.add_audio(make_audio(0.5))
        assert abs(lt.buffer_duration() - 1.5) < 0.01

    def test_buffer_caps_at_max(self, mock_transcriber):
        from ws_server.processing.live_transcriber import LiveTranscriber, MAX_BUFFER_SECONDS
        lt = LiveTranscriber()

        # Add more than max
        lt.add_audio(make_audio(MAX_BUFFER_SECONDS + 5.0))
        assert lt.buffer_duration() <= MAX_BUFFER_SECONDS + 0.01

    def test_has_enough_audio(self, mock_transcriber):
        from ws_server.processing.live_transcriber import LiveTranscriber, MIN_CHUNK_SECONDS
        lt = LiveTranscriber()

        assert lt.has_enough_audio() is False

        lt.add_audio(make_audio(MIN_CHUNK_SECONDS + 0.1))
        assert lt.has_enough_audio() is True

    def test_reset_clears_state(self, mock_transcriber):
        from ws_server.processing.live_transcriber import LiveTranscriber
        lt = LiveTranscriber()
        lt.add_audio(make_audio(2.0))
        lt._confirmed_text = "Hello world"
        lt._prev_text = "Hello world test"

        lt.reset()

        assert lt.buffer_duration() == 0.0
        assert lt._confirmed_text == ""
        assert lt._prev_text == ""





class TestLiveTranscriberProcess:
    """Tests for the async process method."""

    def test_process_returns_none_when_not_enough_audio(self, mock_transcriber):
        from ws_server.processing.live_transcriber import LiveTranscriber
        lt = LiveTranscriber()
        lt.add_audio(make_audio(0.5))  # Less than MIN_CHUNK_SECONDS

        result = asyncio.get_event_loop().run_until_complete(lt.process())
        assert result is None

    def test_process_returns_result_with_enough_audio(self, mock_transcriber):
        from ws_server.processing.live_transcriber import LiveTranscriber
        lt = LiveTranscriber()
        lt.add_audio(make_audio(3.5))

        result = asyncio.get_event_loop().run_until_complete(lt.process())
        assert result is not None
        assert result.language == "de"
        # First call has no prev_text, so confirmed should be empty
        # and partial should be the full text
        assert result.partial == "Hello world this is a test"

    def test_process_twice_produces_confirmed(self, mock_transcriber):
        """When the same text is returned twice, local agreement confirms it."""
        from ws_server.processing.live_transcriber import LiveTranscriber
        lt = LiveTranscriber()

        # First pass
        lt.add_audio(make_audio(3.5))
        result1 = asyncio.get_event_loop().run_until_complete(lt.process())
        assert result1 is not None

        # Second pass - same text returned by model
        lt.add_audio(make_audio(2.0))
        result2 = asyncio.get_event_loop().run_until_complete(lt.process())
        assert result2 is not None
        assert "Hello world this is a test" in result2.confirmed

    def test_flush_finalizes_everything(self, mock_transcriber):
        from ws_server.processing.live_transcriber import LiveTranscriber
        lt = LiveTranscriber()
        lt.add_audio(make_audio(2.0))

        result = asyncio.get_event_loop().run_until_complete(lt.flush())
        assert result is not None
        assert result.partial == ""
        assert "Hello world this is a test" in result.confirmed

    def test_get_full_transcript(self, mock_transcriber):
        from ws_server.processing.live_transcriber import LiveTranscriber
        lt = LiveTranscriber()
        lt.add_audio(make_audio(2.0))
        asyncio.get_event_loop().run_until_complete(lt.flush())

        transcript = lt.get_full_transcript()
        assert "Hello world this is a test" in transcript



class TestLiveTranscriberEvents:
    """Tests for event-driven wakeup mechanism."""

    def test_event_not_set_initially(self, mock_transcriber):
        from ws_server.processing.live_transcriber import LiveTranscriber
        lt = LiveTranscriber()
        assert not lt._audio_event.is_set()
        assert not lt._audio_event_sync.is_set()

    def test_event_set_after_enough_audio(self, mock_transcriber):
        from ws_server.processing.live_transcriber import LiveTranscriber, MIN_NEW_AUDIO_SECONDS
        lt = LiveTranscriber()
        lt.add_audio(make_audio(MIN_NEW_AUDIO_SECONDS + 0.1))
        assert lt._audio_event.is_set()
        assert lt._audio_event_sync.is_set()

    def test_event_not_set_for_tiny_chunk(self, mock_transcriber):
        from ws_server.processing.live_transcriber import LiveTranscriber, MIN_NEW_AUDIO_SECONDS
        lt = LiveTranscriber()
        lt.add_audio(make_audio(MIN_NEW_AUDIO_SECONDS - 0.1))
        assert not lt._audio_event.is_set()

    def test_event_set_after_multiple_small_chunks(self, mock_transcriber):
        """Multiple small chunks that together exceed threshold must fire event."""
        from ws_server.processing.live_transcriber import LiveTranscriber, MIN_NEW_AUDIO_SECONDS
        lt = LiveTranscriber()
        chunk = make_audio(MIN_NEW_AUDIO_SECONDS / 3 + 0.01)
        lt.add_audio(chunk)
        assert not lt._audio_event.is_set()
        lt.add_audio(chunk)
        assert not lt._audio_event.is_set()
        lt.add_audio(chunk)
        # Three chunks together exceed threshold
        assert lt._audio_event.is_set()

    def test_reset_clears_events(self, mock_transcriber):
        from ws_server.processing.live_transcriber import LiveTranscriber
        lt = LiveTranscriber()
        lt.add_audio(make_audio(2.0))
        assert lt._audio_event.is_set()
        lt.reset()
        assert not lt._audio_event.is_set()
        assert not lt._audio_event_sync.is_set()

    def test_last_processed_updated_on_process(self, mock_transcriber):
        from ws_server.processing.live_transcriber import LiveTranscriber
        lt = LiveTranscriber()
        lt.add_audio(make_audio(3.5))
        assert lt._last_processed_buffer_end == 0.0
        asyncio.get_event_loop().run_until_complete(lt.process())
        assert lt._last_processed_buffer_end > 0.0

    def test_no_spurious_event_after_process(self, mock_transcriber):
        """After processing, adding a tiny chunk must not re-fire the event."""
        from ws_server.processing.live_transcriber import LiveTranscriber, MIN_NEW_AUDIO_SECONDS
        lt = LiveTranscriber()
        lt.add_audio(make_audio(3.5))
        asyncio.get_event_loop().run_until_complete(lt.process())
        lt._audio_event.clear()
        lt._audio_event_sync.clear()
        # Add tiny chunk (less than threshold since last process)
        lt.add_audio(make_audio(MIN_NEW_AUDIO_SECONDS / 2))
        assert not lt._audio_event.is_set()