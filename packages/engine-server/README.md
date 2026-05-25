# resona-engine-server

FastAPI HTTP/WS server that exposes a [Resona](https://github.com/ecsplico/resona) ASR engine on `:7001`. Stateless — no database, no postprocessing, no persistent state. Combine with one of the engine plugins (`resona-engine-faster-whisper`, `resona-engine-whisper`, `resona-engine-voxtral`) to get a working transcription service.

See the [main repository](https://github.com/ecsplico/resona) for documentation.
