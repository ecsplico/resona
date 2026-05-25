# Resona

Resona is a modular audio transcription platform with pluggable ASR engines and a composable postprocessing pipeline. Designed for German medical dictation but usable for any language. Built as a uv workspace monorepo.

## Architecture

```
                    ┌──────────────────────┐
                    │     resona CLI       │
                    │  transcribe / watch /│
                    │  rec / live / ui     │
                    └─────────┬────────────┘
                              │ uses
                    ┌─────────▼────────────┐
                    │   resona-client      │
                    │   ResonaClient       │
                    └──┬───────────────┬───┘
                       │               │
            HTTP/REST  │               │  HTTP/REST
                       │               │
          ┌────────────▼──┐   ┌───────▼──────────────┐
          │  resona-api   │   │ resona-engine-server  │
          │  :7000        │──▶│  + engine plugin     │
          │               │   │                       │
          │ POST /jobs    │   │ POST /transcribe      │
          │ GET  /jobs/   │   │ WS   /ws/transcribe   │
          │ GET  /job/id  │   │ WS   /ws/live         │
          │ CRUD replace  │   │                       │
          │ CRUD prompts  │   │ Stateless, no DB      │
          │               │   │ depends on resona-    │
          │ SQLite DB     │   │ asr-core              │
          │ Postprocessing│   └───────────────────────┘
          └───────────────┘
```

## Packages

| Package | Port | Description |
|---------|------|-------------|
| `resona-asr-core` | -- | Lean ASR contracts: protocol, registry, audio, live transcriber |
| `resona-engine-server` | 7001 | FastAPI HTTP/WS server, hosts an ASR engine |
| `resona-engine-faster-whisper` | -- | CTranslate2 engine (default, recommended) |
| `resona-engine-whisper` | -- | Original OpenAI Whisper (PyTorch) engine |
| `resona-engine-voxtral` | -- | HuggingFace Transformers engine (Voxtral, Whisper, etc.) |
| `resona-cloud-stt` | -- | Cloud STT providers: Deepgram, ElevenLabs, OpenAI Whisper API |
| `resona-cloud-tts` | -- | Cloud TTS providers: OpenAI, ElevenLabs, Deepgram |
| `resona-postprocess` | -- | Composable pipeline: regex replacements + LLM via litellm |
| `resona-api` | 7000 | Job queue + SQLite + postprocessing, calls engine via HTTP |
| `resona-client` | -- | httpx client library for the resona-api REST interface |
| `resona-cli` | -- | CLI: `resona transcribe/watch/rec/live/ui/engines/replacements/prompts` |

## Quick start

### Local-only (no server)

```bash
uv sync --all-packages
uv run resona transcribe recording.mp3
```

No server needed — the CLI spawns a local `faster-whisper` engine automatically.

### Docker

```bash
cp .env.example .env   # set RESONA_API_KEY at minimum
docker compose -f docker-compose.resona.yml up -d
resona transcribe recording.mp3
```

For end-user install (`uv tool install`), PyTorch extras, macOS, and per-persona variants see [Installation](https://ecsplico.github.io/resona/getting-started/installation/).

> Full documentation: https://ecsplico.github.io/resona/

## License

Apache License 2.0 — see [LICENSE](LICENSE).
