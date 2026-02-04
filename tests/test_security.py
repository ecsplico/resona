"""
Unit tests for security utilities (filename sanitization, validation).
"""
import pytest
import sys
from pathlib import Path

# Add src to path for testing
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ws_server.api.endpoints import sanitize_filename


class TestFilenameSanitization:
    """Test filename sanitization for security."""
    
    def test_basic_filename(self):
        """Test that normal filenames pass through."""
        assert sanitize_filename("test_file.mp3") == "test_file.mp3"
        assert sanitize_filename("audio-123.wav") == "audio-123.wav"
    
    def test_path_traversal_attack(self):
        """Test that path traversal attempts are blocked."""
        # Absolute path
        result = sanitize_filename("/etc/passwd")
        assert result == "passwd"
        
        # Relative path traversal
        result = sanitize_filename("../../etc/passwd")
        assert result == "passwd"
        
        # Windows path
        result = sanitize_filename("C:\\Windows\\System32\\config.sys")
        assert result == "config.sys"
    
    def test_special_characters(self):
        """Test that special characters are replaced."""
        result = sanitize_filename("file with spaces.mp3")
        assert result == "file_with_spaces.mp3"
        
        result = sanitize_filename("file@#$%.mp3")
        assert result == "file____.mp3"
    
    def test_empty_or_invalid_names(self):
        """Test handling of empty or invalid filenames."""
        assert sanitize_filename("") == "unnamed_file"
        assert sanitize_filename(".") == "unnamed_file"
        assert sanitize_filename("..") == "unnamed_file"
    
    def test_unicode_filename(self):
        """Test handling of unicode characters."""
        result = sanitize_filename("日本語.mp3")
        assert ".mp3" in result  # Extension preserved
    
    def test_preserves_extension(self):
        """Test that file extensions are preserved."""
        assert sanitize_filename("audio.mp3").endswith(".mp3")
        assert sanitize_filename("test.wav").endswith(".wav")
