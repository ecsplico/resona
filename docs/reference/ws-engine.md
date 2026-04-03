# resona-engine-core Internals

Internal reference for `resona-engine-core` — the stateless transcription service.

!!! note "Legacy"
    The older `ws-engine` package is retained for backward compatibility. This page documents `resona-engine-core` (`packages/engine-core/`). Legacy module paths use `ws_engine.*`.

## Transcriber protocol

All backends implement the `Transcriber` protocol defined in `resona_engine_core.protocol`:

::: resona_engine_core.protocol.Transcriber

::: resona_engine_core.protocol.TranscriptionResult

## Backend registry

Backends are discovered via Python entry points at startup. The `RESONA_BACKEND` environment variable selects which backend to load.

::: resona_engine_core.registry.get_transcriber

::: resona_engine_core.registry.list_backends

## Available backends

### faster-whisper (default)

::: resona_engine_faster_whisper.transcriber.FastWhisperTranscriber

### openai-whisper

::: resona_engine_whisper.transcriber.WhisperTranscriber

## Audio utilities

::: resona_engine_core.audio.load_audio

## WebSocket streaming

::: resona_engine_core.ws_transcribe.AudioBuffer
