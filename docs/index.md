# Resona

Modular audio transcription platform with pluggable ASR engines and a composable postprocessing pipeline. Designed for German medical dictation — usable for any language.

Works standalone (no server needed), against a self-hosted stack, or with cloud STT/TTS providers. Engines are discovered via Python entry points; postprocessing is a separate layer the engine never sees.

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
          ┌────────────▼──┐   ┌───────▼──────────────────────┐
          │  resona-api   │   │ resona-engine-server          │
          │  :7000        │──▶│  + engine plugin              │
          │               │   │                               │
          │ POST /jobs    │   │ POST /transcribe              │
          │ GET  /jobs/   │   │ WS   /ws/transcribe           │
          │ GET  /job/id  │   │ WS   /ws/live                 │
          │ CRUD replace  │   │                               │
          │ CRUD prompts  │   │ Stateless, no DB              │
          │               │   └───────────────────────────────┘
          │ SQLite DB     │
          │ Postprocessing│
          └───────────────┘
```

## Packages

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

    Full stack with job queue, persistence, and postprocessing.

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
