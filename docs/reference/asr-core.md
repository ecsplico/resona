# resona-asr-core

`resona-asr-core` is the lean shared foundation for all Resona ASR engines. It defines the `Transcriber` protocol that every backend must satisfy, the `TranscriptionResult` type they return, audio loading utilities, engine discovery and singleton management, and the `LiveTranscriber` for VAD-based streaming transcription.

This package has no FastAPI dependency and no web framework — it is a pure library imported by both engine packages and by `resona-cli` for in-process transcription.

## The Transcriber contract

All ASR backends implement the `Transcriber` protocol. You never need to instantiate a backend directly — use `get_transcriber()` which discovers and loads the engine registered under `RESONA_ENGINE`.

```python
from resona_asr_core.registry import get_transcriber
from resona_asr_core.audio import load_audio

transcriber = get_transcriber()           # loads RESONA_ENGINE (default: faster-whisper)

with open("audio.wav", "rb") as f:
    audio = load_audio(f)                 # float32 mono waveform at 16 kHz

result = transcriber.transcribe(audio, language="de")
print(result["text"])
print(result["language"])
print(result["segments"])
```

## Engine discovery

Engines register themselves in their `pyproject.toml` under the `resona.engines` entry-point group:

```toml
[project.entry-points."resona.engines"]
faster-whisper = "resona_engine_faster_whisper.transcriber:FastWhisperTranscriber"
```

`get_transcriber()` uses `importlib.metadata.entry_points` to discover all installed engines at runtime and returns a thread-safe singleton. Set `RESONA_ENGINE` to select a non-default engine.

## Audio format

All engines expect audio as a **mono float32 numpy array at 16 kHz**. `load_audio()` handles conversion from any format that ffmpeg understands (mp3, wav, m4a, ogg, flac, aac, webm, ...).

```python
SAMPLE_RATE = 16000  # Hz — the only sample rate accepted by engine backends
```

## Writing a new engine

Implement a class that satisfies `Transcriber`:

```python
class MyEngine:
    def __init__(self, device: str, modelname: str | None = None) -> None:
        ...

    def transcribe(self, audio: np.ndarray, **kwargs) -> TranscriptionResult:
        ...
```

Then register it via `pyproject.toml` entry points and install into the workspace. `get_transcriber("my-engine")` will find it automatically.

---

## Transcriber protocol

::: resona_asr_core.protocol.Transcriber

---

## TranscriptionResult

::: resona_asr_core.protocol.TranscriptionResult

---

## Audio utilities

`SAMPLE_RATE` is the constant `16000`. All engine backends expect audio resampled to this rate.

::: resona_asr_core.audio.load_audio

---

## Registry

::: resona_asr_core.registry.get_transcriber

::: resona_asr_core.registry.reset
