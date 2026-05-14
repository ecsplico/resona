# Resona

Audio transcription system built on OpenAI Whisper / faster-whisper, designed for German medical dictation. Structured as a uv workspace monorepo with independently deployable services.

## Architecture

```
                ┌──────────────────────┐
                │     resona (CLI)     │
                │  transcribe / watch /│
                │  rec / live / ui     │
                └─────────┬────────────┘
                          │ uses
                ┌─────────▼────────────┐
                │    resona-client     │
                │    ResonaClient      │
                └──┬───────────────┬───┘
                   │               │
        HTTP/REST  │               │  HTTP/REST
                   │               │
      ┌────────────▼──┐   ┌───────▼──────────────────────┐
      │  resona-api   │   │  resona-engine-core           │
      │  :7000        │──▶│  + backend package  :7001     │
      │               │   │                              │
      │ POST /jobs    │   │ POST /transcribe             │
      │ GET  /jobs/   │   │ WS   /ws/transcribe          │
      │ GET  /jobs/id │   │ WS   /ws/live                │
      │ CRUD replace  │   │                              │
      │ CRUD prompts  │   │ Stateless, no DB             │
      │               │   │ GPU, heavy deps              │
      │ SQLite DB     │   └──────────────────────────────┘
      │ File storage  │
      └───────────────┘
```

**resona-engine-core** is stateless — no database, no side effects. It owns all GPU-heavy inference. Backends are installed as separate packages (`resona-engine-faster-whisper`, `resona-engine-whisper`) and discovered via entry points.

**resona-api** owns the job queue, SQLite database, and calls the engine over HTTP. Post-processing (replacements, formatting) is handled by `resona-postprocess` in the API layer — the engine returns raw transcripts.

This separation lets the engine run on a dedicated GPU machine while the API runs elsewhere.

## Features

- **Multiple ASR backends** — faster-whisper, openai-whisper, HuggingFace Transformers; installed as separate packages, discovered via entry points
- **Async job queue** — submit audio files, poll for results, never block the caller
- **Text replacements** — regex-based post-processing applied by the API (`resona-postprocess`)
- **Initial prompts** — Whisper vocabulary hints stored per backend
- **Live transcription** — VAD-chunked WebSocket streaming at 16 kHz
- **TUI tools** — Textual-based recorder (`rec`), live UI (`live`), record-and-transcribe (`ui`)
- **Backend config** — priority-ordered multi-server with SSH tunnel and Docker auto-start

## Quick links

- [CLI Setup & Onboarding](onboarding.md) — install `resona` CLI and connect to a server
- [Server Setup](getting-started.md) — run the services locally or via Docker
- [Architecture](architecture.md) — service design and job lifecycle
- [CLI Reference](cli.md) — all `resona` commands
- [Backends & SSH](configuration/backends.md) — LAN, SSH tunnel, auto-start
- [Environment Variables](configuration/environment.md) — all config knobs
- [Client Library](reference/client.md) — Python API reference

!!! note "Legacy packages"
    The old `ws-engine`, `ws-api`, `ws-client`, and `ws-cli` packages are retained for backward compatibility but are considered legacy. New deployments should use the `resona-*` packages.
