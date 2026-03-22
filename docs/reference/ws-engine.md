# ws-engine Internals

Internal reference for `ws-engine` — the stateless transcription service.

## Transcription backends

Backends are selected via the `ASR_MODE` environment variable and cached as a singleton by `transcriber_factory`.

::: ws_engine.transcriber_factory.getTranscriber

::: ws_engine.transcriber_fast_whisper.FastWhisperTranscriber

::: ws_engine.transcriber_whisper.WhisperTranscriber

::: ws_engine.transcriber_transformer.TransformerTranscriber

## Audio utilities

::: ws_engine.utils.run_asr

::: ws_engine.utils.load_audio

## Replacements

::: ws_engine.replacements.apply_replacements

## WebSocket streaming

::: ws_engine.ws_transcribe.AudioBuffer
