# Architecture Overview

Resona is a modular transcription platform composed of loosely coupled packages. Each package has a single clear responsibility. Cross-service communication is over HTTP; the engine never sees the database, and the database never runs inference.

## Package table

| Package | Port | Role | GPU |
|---------|------|------|-----|
| `resona-asr-core` | — | Lean ASR contracts: Transcriber protocol, registry, audio utilities, live transcriber | — |
| `resona-engine-server` + engine | 7001 | Stateless transcription server; hosts one engine plugin | Required |
| `resona-engine-faster-whisper` | — | CTranslate2 engine (default, recommended) | Yes |
| `resona-engine-whisper` | — | Original OpenAI Whisper (PyTorch) | Yes |
| `resona-engine-voxtral` | — | HuggingFace Transformers (Voxtral, Whisper, etc.) | Yes |
| `resona-cloud-stt` | — | Cloud STT providers: Deepgram, ElevenLabs, OpenAI | — |
| `resona-cloud-tts` | — | Cloud TTS providers: Deepgram, ElevenLabs, OpenAI | — |
| `resona-postprocess` | — | Replacements + LLM pipeline | — |
| `resona-api` | 7000 | Job queue, SQLite DB, postprocessing, OpenAI-compatible audio API | — |
| `resona-client` | — | Python client library for resona-api | — |
| `resona-cli` | — | CLI and Textual TUI tools | — |

## Architecture diagram

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
          │  :7000        │──▶│  + engine plugin      │
          │               │   │                       │
          │ POST /jobs    │   │ POST /transcribe      │
          │ GET  /jobs/   │   │ WS   /ws/transcribe   │
          │ GET  /job/id  │   │ WS   /ws/live         │
          │ CRUD replace  │   │                       │
          │ CRUD prompts  │   │ Stateless, no DB      │
          │               │   └───────────────────────┘
          │ SQLite DB     │
          │ Postprocessing│
          └───────────────┘
```

## Layer descriptions

### resona-cli (apps/resona-cli)

The user-facing entry point. Provides subcommands for transcription (`transcribe`, `watch`), recording TUIs (`rec`, `ui`), live transcription (`live`), speech synthesis (`speech`), and CRUD for engines, replacements, and prompts. Delegates all server operations to `resona-client`. In local-only mode it can run an engine in-process without any server.

### resona-client

A thin `httpx`-based client that wraps every resona-api REST operation. The CLI imports this for all HTTP work. Configuration is read from `RESONA_API_URL` / `RESONA_API_KEY` environment variables.

### resona-api (:7000)

The coordination layer. Accepts audio uploads, persists jobs to SQLite, polls for pending work, calls the engine, applies postprocessing, and stores results. Also exposes an OpenAI-compatible audio API (`GET /v1/engines`, `POST /v1/audio/transcriptions`, `POST /v1/audio/speech`) that can route requests to local engines or cloud providers.

### resona-engine-server + engine plugin (:7001)

A stateless FastAPI application that loads exactly one engine plugin at startup (selected by `RESONA_ENGINE`). Accepts audio, runs inference, returns raw `{text, language, segments}`. No database. No postprocessing. See [Stateless Engine Contract](engine-contract.md) for the full design rationale.

### resona-asr-core

The shared vocabulary: `Transcriber` Protocol, `TranscriptionResult` TypedDict, the entry-point registry (`registry.py`), audio loading utilities (`audio.py`), and the VAD-based live transcriber (`live_transcriber.py`). Both the engine server and the CLI's `InProcessEngine` depend on this package.

### resona-postprocess

A composable `str → str` pipeline: regex replacement steps and LLM steps (via litellm). Runs in `TranscribeTask` on the API side and in the CLI process in local mode. See [Postprocessing Pipeline](postprocessing.md).

### resona-cloud-stt / resona-cloud-tts

Provider adapters for cloud speech services. No persistent state; each call is self-contained. Activated automatically when the corresponding API key environment variable is present.

## Cross-package import rules

Only the following direct cross-package imports are permitted. All other cross-service communication must go over HTTP.

```
resona-cli  →  resona_asr_core.live_transcriber   (live command; base dependency)
resona-cli  →  resona_asr_core.registry           (InProcessEngine; base dependency)
resona-cli  →  resona_client.client               (all HTTP operations)
```

!!! warning "Never import across service boundaries"
    `resona-cli` and `resona-client` must never import from `resona-api` or `resona-engine-server`.
    `resona-engine-server` must never import from `resona-api`.
    Import the package over HTTP, not as a Python module.

## Data storage layout

All server-side data lives under `DATA_PATH` (default `./data`):

```
DATA_PATH/
├── files/      ← uploaded audio files (never deleted by the engine)
├── db/         ← SQLite database (jobs, replacements, initial prompts)
└── md/         ← generated Markdown transcript files
```

`DATA_PATH`, `FILE_PATH`, `DB_PATH`, and `MD_PATH` are resolved in `resona_api/paths.py` and read via `python-decouple` so they can be overridden by environment variables or a `.env` file.
