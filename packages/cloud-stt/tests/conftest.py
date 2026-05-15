"""Shared fixtures for resona-cloud-stt tests."""
import io
import struct
import wave
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def wav_path() -> Path:
    """A tiny valid WAV file (160 frames silence, 16kHz mono) under tests/fixtures/."""
    FIXTURES.mkdir(exist_ok=True)
    path = FIXTURES / "silence.wav"
    if not path.exists():
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(struct.pack("<" + "h" * 160, *([0] * 160)))
        path.write_bytes(buf.getvalue())
    return path
