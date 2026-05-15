# resona-asr-core / resona-engine-server Internals

Internal reference for `resona-asr-core` (lean ASR contracts) and `resona-engine-server` (FastAPI HTTP/WS server).

## Transcriber protocol

All engines implement the `Transcriber` protocol defined in `resona_asr_core.protocol`:

::: resona_asr_core.protocol.Transcriber

::: resona_asr_core.protocol.TranscriptionResult

## Engine registry

Engines are discovered via Python entry points at startup. The `RESONA_ENGINE` environment variable selects which engine to load.

::: resona_asr_core.registry.get_transcriber

## Available engines

### faster-whisper (default)

::: resona_engine_faster_whisper.transcriber.FastWhisperTranscriber

### openai-whisper

::: resona_engine_whisper.transcriber.WhisperTranscriber

## Audio utilities

::: resona_asr_core.audio.load_audio

## WebSocket streaming

::: resona_engine_server.ws_transcribe.AudioBuffer
