# whisper-server

Audio transcription system built on OpenAI Whisper / faster-whisper, designed for German medical dictation. Structured as a uv workspace monorepo with independently deployable services.

## Architecture

```
                ┌──────────────────────┐
                │     ws-cli           │
                │  batch / watch /     │
                │  rec / live / ui     │
                └─────────┬────────────┘
                          │ uses
                ┌─────────▼────────────┐
                │     ws-client        │
                │  WhisperClient       │
                └──┬───────────────┬───┘
                   │               │
        HTTP/REST  │               │  HTTP/REST
                   │               │
      ┌────────────▼──┐   ┌───────▼──────────────┐
      │   ws-api      │   │   ws-engine           │
      │   :7000       │──▶│   :7001               │
      │               │   │                       │
      │ POST /jobs    │   │ POST /transcribe      │
      │ GET  /jobs/   │   │ WS   /ws/transcribe   │
      │ GET  /jobs/id │   │ WS   /ws/live         │
      │ CRUD replace  │   │                       │
      │ CRUD prompts  │   │ Stateless, no DB      │
      │               │   │ GPU, heavy deps       │
      │ SQLite DB     │   └───────────────────────┘
      │ File storage  │
      └───────────────┘
```

**ws-engine** is stateless — no database, no side effects. It owns all GPU-heavy inference.
**ws-api** owns the job queue, SQLite database, and calls the engine over HTTP.
This separation lets the engine run on a dedicated GPU machine while the API runs elsewhere.

## Features

- **Multiple ASR backends** — faster-whisper, openai-whisper, HuggingFace Transformers
- **Async job queue** — submit audio files, poll for results, never block the caller
- **Text replacements** — regex-based post-processing (spoken punctuation → symbols)
- **Initial prompts** — Whisper vocabulary hints stored per backend
- **Live transcription** — VAD-chunked WebSocket streaming at 16 kHz
- **TUI tools** — Textual-based recorder (`rec`), live UI (`live`), record-and-transcribe (`ui`)
- **Backend config** — priority-ordered multi-server with SSH tunnel and Docker auto-start

## Quick links

- [Getting Started](getting-started.md) — install, run, first transcription
- [Architecture](architecture.md) — service design and job lifecycle
- [CLI Reference](cli.md) — all `ws-cli` commands
- [Backends & SSH](configuration/backends.md) — remote server setup
- [Environment Variables](configuration/environment.md) — all config knobs
- [Client Library](reference/client.md) — Python API reference
