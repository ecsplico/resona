import pytest
import sys
from unittest.mock import MagicMock, patch

def test_transcriber_import_and_initialization():
    """Test that the transcriber can be imported and initialized."""
    # We mock the actual loading of the model to avoid downloading/loading heavy weights during tests
    with patch("ws_server.processing.transcriber_fast_whisper.FastWhisperTranscriber") as MockTranscriber:
        # Import inside the test to ensure environment is set up (via conftest)
        try:
            from ws_server.processing.transcriber_fast_whisper import FastWhisperTranscriber
            assert True, "Import successful"
        except ImportError as e:
            pytest.fail(f"Import failed: {e}")

        # Test initialization
        try:
            # Configure the mock to return a valid object
            mock_instance = MockTranscriber.return_value
            mock_instance.modelname = "base"
            
            transcriber = FastWhisperTranscriber()
            assert transcriber.modelname == "base"
        except Exception as e:
            pytest.fail(f"Initialization failed: {e}")
