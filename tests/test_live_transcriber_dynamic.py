"""Tests for the LiveTranscriber dynamic buffering with 10s retention."""
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

class TestDynamicRetention:
    """Tests for 10s safety retention and overlap deduplication."""

    def test_retention_under_10s_keeps_everything(self, mock_transcriber_factory):
        from ws_server.processing.live_transcriber import LiveTranscriber, SAMPLE_RATE
        
        mock_t = MagicMock()
        mock_transcriber_factory.return_value = mock_t
        
        # confirm "Hello world" (1.0s)
        # We start with 2.0s audio.
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
        lt.add_audio(make_audio(3.5))
        lt._prev_text = "Hello" # Triggers confirmation of "Hello"
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(lt.process())
        
        # 1. Confirmed "Hello"
        assert "Hello" in result.confirmed
        
        # 2. Retention
        # Confirmed end is 0.5s.
        # Target cut = max(0, 0.5 - 10.0) = 0.0.
        # Should keep everything.
        expected_samples = int(3.5 * SAMPLE_RATE)
        assert len(lt.buffer) == expected_samples
        
        # 3. Emitted word count
        # Since we kept everything (start=0.0), retained_confirmed = ["Hello"].
        # So _emitted_word_count should be 1.
        assert lt._emitted_word_count == 1

    def test_retention_slice_over_10s(self, mock_transcriber_factory):
        from ws_server.processing.live_transcriber import LiveTranscriber, SAMPLE_RATE
        
        mock_t = MagicMock()
        mock_transcriber_factory.return_value = mock_t
        
        # Confirm a long segment: 15 seconds.
        # Words every 1s.
        words = []
        for i in range(15):
             words.append(SimpleNamespace(word=f"W{i}", start=float(i), end=float(i+1)))
             
        mock_result = {
            "text": " ".join([w.word for w in words]),
            "language": "en",
            "segments": [SimpleNamespace(words=words)]
        }
        mock_t.transcribe.return_value = mock_result

        lt = LiveTranscriber(language="en")
        lt.add_audio(make_audio(20.0)) # 20s buffer
        lt._prev_text = " ".join([w.word for w in words]) # Confirm everything
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(lt.process())
        
        # 1. All confirmed
        assert "W14" in result.confirmed
        
        # 2. Slice
        # Last confirmed end = 15.0s.
        # Target cut = 15.0 - 10.0 = 5.0s.
        # Closest word start > 5.0s?
        # W5 starts at 5.0. W4 ends at 5.0.
        # Loop: if word.end > 5.0.
        # W5 ends at 6.0 > 5.0.
        # So t_actual_cut = W5.start = 5.0.
        
        # Buffer should be sliced by 5.0s.
        # Starting length 20.0s. Result 15.0s.
        expected_samples = int((20.0 - 5.0) * SAMPLE_RATE)
        assert abs(len(lt.buffer) - expected_samples) < 1000
        
        # 3. Emitted word count
        # Retained words are W5..W14 (10 words).
        assert lt._emitted_word_count == 10

    def test_deduplication_emit(self, mock_transcriber_factory):
        from ws_server.processing.live_transcriber import LiveTranscriber
        
        mock_t = MagicMock()
        mock_transcriber_factory.return_value = mock_t
        
        # Step 1: Pre-condition - we have overlap "Hello world"
        lt = LiveTranscriber()
        lt._emitted_word_count = 2  # "Hello world" already emitted
        lt._confirmed_text = "Already committed Hello world" # Simulating history
        
        # Step 2: Input continues
        # "Hello world this is new"
        # 0-1s: Hello world
        # 1-2s: this is new
        words = [
            SimpleNamespace(word="Hello", start=0.0, end=0.5),
            SimpleNamespace(word="world", start=0.5, end=1.0),
            SimpleNamespace(word="this", start=1.0, end=1.5),
            SimpleNamespace(word="is", start=1.5, end=2.0),
            SimpleNamespace(word="new", start=2.0, end=2.5),
        ]
        
        mock_result = {
            "text": "Hello world this is new",
            "language": "en",
            "segments": [SimpleNamespace(words=words)]
        }
        mock_t.transcribe.return_value = mock_result
        
        lt.add_audio(make_audio(5.0))
        # Confirm everything
        lt._prev_text = "Hello world this is new"
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(lt.process())
        
        # 1. Emission check
        # Should only emit "this is new"
        # Because "Hello world" matches overlap.
        # Result.confirmed is global accumulator.
        # It should end with "this is new".
        # It should NOT contain "Hello world Hello world".
        
        expected_suffix = "already committed hello world this is new"
        assert result.confirmed.lower().endswith("this is new")
        assert result.confirmed.lower().count("hello world") == 1 # Only the one in history
        
        # 2. Emitted word count check
        # Last end = 2.5s. target = max(0, 2.5 - 10) = 0 -> keep everything.
        # All 5 words retained, so _emitted_word_count = 5.
        assert lt._emitted_word_count == 5

    def test_flush_deduplicates_retained_audio(self, mock_transcriber_factory):
        """flush() must not re-emit words from the retention window."""
        from ws_server.processing.live_transcriber import LiveTranscriber

        mock_t = MagicMock()
        mock_transcriber_factory.return_value = mock_t

        lt = LiveTranscriber(language="en")

        # Simulate state after several process() cycles:
        # "Hello world this is" was confirmed, buffer still has retained audio
        lt._confirmed_text = "Hello world this is"
        lt._emitted_word_count = 4  # 4 words already emitted
        lt.add_audio(make_audio(5.0))  # retained audio in buffer

        # flush() transcribes the retained buffer and gets back the
        # retained words plus maybe a new word "great"
        words = [
            SimpleNamespace(word="Hello", start=0.0, end=0.5),
            SimpleNamespace(word="world", start=0.5, end=1.0),
            SimpleNamespace(word="this", start=1.0, end=1.5),
            SimpleNamespace(word="is", start=1.5, end=2.0),
            SimpleNamespace(word="great", start=2.0, end=2.5),
        ]
        mock_t.transcribe.return_value = {
            "text": "Hello world this is great",
            "language": "en",
            "segments": [SimpleNamespace(words=words)],
        }

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(lt.flush())

        # Only "great" should be the new delta
        assert result.confirmed_delta == "great"
        # Full confirmed should end with "great", no duplication
        assert result.confirmed == "Hello world this is great"
        assert result.confirmed.lower().count("hello") == 1

    def test_flush_sync_deduplicates_retained_audio(self, mock_transcriber_factory):
        """flush_sync() must not re-emit words from the retention window."""
        from ws_server.processing.live_transcriber import LiveTranscriber

        mock_t = MagicMock()
        mock_transcriber_factory.return_value = mock_t

        lt = LiveTranscriber(language="en")
        lt._confirmed_text = "Hello world this is"
        lt._emitted_word_count = 4
        lt.add_audio(make_audio(5.0))

        words = [
            SimpleNamespace(word="Hello", start=0.0, end=0.5),
            SimpleNamespace(word="world", start=0.5, end=1.0),
            SimpleNamespace(word="this", start=1.0, end=1.5),
            SimpleNamespace(word="is", start=1.5, end=2.0),
            SimpleNamespace(word="great", start=2.0, end=2.5),
        ]
        mock_t.transcribe.return_value = {
            "text": "Hello world this is great",
            "language": "en",
            "segments": [SimpleNamespace(words=words)],
        }

        result = lt.flush_sync()

        assert result.confirmed_delta == "great"
        assert result.confirmed == "Hello world this is great"
        assert result.confirmed.lower().count("hello") == 1

    def test_process_sync_matches_async(self, mock_transcriber_factory):
        """process_sync() should produce the same result as process()."""
        from ws_server.processing.live_transcriber import LiveTranscriber

        mock_t = MagicMock()
        mock_transcriber_factory.return_value = mock_t

        words = [
            SimpleNamespace(word="Hello", start=0.0, end=0.5),
            SimpleNamespace(word="world", start=0.5, end=1.0),
        ]
        mock_result = {
            "text": "Hello world",
            "language": "en",
            "segments": [SimpleNamespace(words=words)],
        }
        mock_t.transcribe.return_value = mock_result

        # Async version
        lt_async = LiveTranscriber(language="en")
        lt_async.add_audio(make_audio(3.5))
        lt_async._prev_text = "Hello"

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result_async = loop.run_until_complete(lt_async.process())

        # Sync version
        lt_sync = LiveTranscriber(language="en")
        lt_sync.add_audio(make_audio(3.5))
        lt_sync._prev_text = "Hello"

        result_sync = lt_sync.process_sync()

        assert result_async.confirmed == result_sync.confirmed
        assert result_async.partial == result_sync.partial
        assert result_async.confirmed_delta == result_sync.confirmed_delta

    def test_deduplication_punctuation_ignored(self, mock_transcriber_factory):
        from ws_server.processing.live_transcriber import LiveTranscriber
        
        mock_t = MagicMock()
        mock_transcriber_factory.return_value = mock_t
        
        # Preattempt: Overlap is "Hello." (with period)
        lt = LiveTranscriber()
        lt._emitted_word_count = 1  # "Hello." already emitted
        lt._confirmed_text = "Hello."
        
        # New input: "Hello, world" (comma instead of period)
        words = [
            SimpleNamespace(word="Hello,", start=0.0, end=0.5), # Changed punct
            SimpleNamespace(word="world", start=0.5, end=1.0),
        ]
        
        mock_result = {
            "text": "Hello, world",
            "language": "en",
            "segments": [SimpleNamespace(words=words)]
        }
        mock_t.transcribe.return_value = mock_result
        
        lt.add_audio(make_audio(3.5))
        lt._prev_text = "Hello, world" # Confirm
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(lt.process())
        
        # Should deduplicate "Hello," against "Hello." using normalization
        # And emit ONLY "world" (or maybe " world")
        # Confirmed text should be "Hello. world" (or similar)
        # Definitely NOT "Hello. Hello, world"
        
        assert "Hello." in result.confirmed
        # We expect "world" to be appended.
        assert result.confirmed.endswith("world")
        # Count "Hello"
        assert result.confirmed.lower().count("hello") == 1
