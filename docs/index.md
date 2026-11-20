# Resona

Modular audio transcription platform with pluggable ASR engines and a composable postprocessing pipeline. Designed for German medical dictation — usable for any language.

Works standalone (no server needed), against a self-hosted hub, or with cloud STT/TTS providers. Engines are discovered via Python entry points; postprocessing is a separate layer the engine never sees.

## Two layers

Resona has two layers that meet only at a published contract ([ADR 0001: Hub and app boundary](adr/0001-hub-and-app-boundary.md)):

- **The hub** is a transformation layer: `resona-api`, pull workers, engines, postprocessing, CLI and the job MCP server. It turns uploads into results (jobs), bridges live audio and holds profiles, execution policy, workers and evaluation. It knows organizations and credentials, not users, and keeps a job's result for 24 hours and nothing else on behalf of an app. Most of this site documents the hub.
- **The reference app** (`apps/resona-pwa`: PWA, Android/iOS shell, Directus data layer, project worker) is one maintained consumer of that contract. It owns everything a user keeps: users, dictations, recordings, transcripts, documents, long-term storage and the UI. It gets no access another app could not have. See [Resona app](guide/pwa-app.md) and [PWA backups](server/pwa-backups.md).

```
┌──────────────────────────────────────────────────────────────┐
│ Reference app  (apps/resona-pwa; any other app alike)        │
│ PWA / Android / iOS, Directus, project worker                │
│ owns users, dictations, recordings, transcripts,             │
│ documents, long-term storage and the UI                      │
└────┬──────────────────┬───────────────┬────────────▲─────────┘
     │ jobs             │ live          │ profiles   │ push
     │ POST /v1/uploads │ WS /v1/listen │ GET/PUT    │ signed webhook
     │ POST /v1/jobs    │               │ /profiles  │ on a terminal
     │ pull: GET        │               │            │ job state
     │ /v1/jobs/{id}    │               │            │ (being added)
     ▼                  ▼               ▼            │
┌────────────────────────────────────────────────────┴─────────┐
│ Hub  (resona-api + workers + engines)                        │
│ owns uploads, jobs, live streaming, profiles, policy,        │
│ workers, evaluation; knows organizations and                 │
│ credentials, not users; keeps a job result 24 h              │
└──────────────────────────────────────────────────────────────┘
     only connection: the published HTTP + WebSocket contract
```

Pull (`GET /v1/jobs/{id}`) stays the source of truth; push (signed webhooks for terminal job states) comes on top of it. An app that wants to keep a result fetches and stores it itself.

## Hub architecture

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
          ┌────────────▼──┐   ┌───────▼──────────────────────┐
          │  resona-api   │   │ resona-engine-server          │
          │  :7000        │──▶│  + engine plugin              │
          │               │   │                               │
          │ /v1/uploads   │   │ POST /transcribe              │
          │ /v1/jobs      │   │ WS   /ws/transcribe           │
          │ /v1/workers/* │   │ WS   /ws/live                 │
          │ CRUD replace  │   │                               │
          │ CRUD prompts  │   │ Stateless, no DB              │
          │               │   └───────────────────────────────┘
          │ SQLite DB     │
          │ Postprocessing│
          └───────────────┘
```

## Hub packages

| Package | Port | Description |
|---------|------|-------------|
| `resona-asr-core` | — | Lean ASR contracts: protocol, registry, audio, live transcriber |
| `resona-engine-server` + engine | 7001 | Stateless transcription server (GPU); depends on asr-core |
| `resona-engine-faster-whisper` | — | CTranslate2 engine (default, recommended) |
| `resona-engine-whisper` | — | Original OpenAI Whisper (PyTorch) engine |
| `resona-engine-voxtral` | — | HuggingFace Transformers engine |
| `resona-cloud-stt` | — | Cloud STT: Deepgram, ElevenLabs, OpenAI |
| `resona-cloud-tts` | — | Cloud TTS: Deepgram, ElevenLabs, OpenAI |
| `resona-postprocess` | — | Replacements + LLM pipeline |
| `resona-api` | 7000 | Job queue, SQLite, postprocessing, OpenAI-compatible audio API |
| `resona-client` | — | Python client library for resona-api |
| `resona-cli` | — | CLI: transcribe, watch, rec, live, ui, speech, engines, replacements, prompts |

## Three paths to first transcription

=== "Local-only"

    No server required. The CLI spawns the engine in-process.

    ```bash
    uv tool install --from ./apps/resona-cli resona-cli
    resona transcribe recording.mp3
    ```

    Output is written next to the input file as `.txt`.

=== "Docker"

    Full hub with job queue, pull workers and postprocessing.

    ```bash
    docker compose -f docker-compose.resona.yml up
    resona transcribe recording.mp3   # CLI auto-connects to :7000
    ```

=== "Cloud"

    Use a cloud STT provider — no GPU needed.

    ```bash
    export OPENAI_API_KEY=sk-...
    resona transcribe recording.mp3 --engine openai
    ```

---

[Get started →](getting-started/installation.md)
