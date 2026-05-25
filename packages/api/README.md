# resona-api

Job queue, SQLite database, and postprocessing API for [Resona](https://github.com/ecsplico/resona). Exposes both the native job/profile/transcript REST endpoints and an OpenAI-compatible `/v1/audio/transcriptions` and `/v1/audio/speech` surface on `:7000`. Routes jobs to local engine-server backends or cloud STT/TTS providers and applies profile pipelines after transcription.

See the [main repository](https://github.com/ecsplico/resona) for documentation.
