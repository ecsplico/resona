import os
import sys
import shutil
import tempfile
from pathlib import Path
import pytest

# Set up test environment before any other imports
# We need to set DATA_PATH to a temporary directory
# This must happen before importing any modules that use DATA_PATH or DB_PATH
test_data_dir = tempfile.mkdtemp(prefix="whisper_test_data_")
os.environ["DATA_PATH"] = test_data_dir

# Also set fake API key for testing if not set
if "API_KEY" not in os.environ:
    os.environ["API_KEY"] = "test_key_fixture"

@pytest.fixture(scope="session", autouse=True)
def setup_teardown_test_env():
    """Setup and teardown the test environment."""
    yield
    # Cleanup after all tests are done
    shutil.rmtree(test_data_dir, ignore_errors=True)

@pytest.fixture
def mock_transcriber():
    """Mock the transcriber for faster testing."""
    from unittest.mock import MagicMock
    mock = MagicMock()
    mock.transcribe.return_value = {"text": "Test transcript"}
    return mock
