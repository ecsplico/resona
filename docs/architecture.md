# Architecture

## Service overview

| Package | Port | Role | GPU |
|---------|------|------|-----|
| `resona-asr-core` | — | Lean ASR contracts: protocol, registry, audio, live transcriber | — |
| `resona-engine-server` + engine | 7001 | Stateless transcription (inference); depends on asr-core | Required |
| `resona-api` | 7000 | Job queue, SQLite DB, file storage, postprocessing | No |
| `resona-cloud-stt` | — | Cloud STT providers (Deepgram, ElevenLabs, OpenAI); httpx-only, no torch | — |
| `resona-client` | — | Python client library | — |
| `resona` CLI (`apps/resona-cli`) | — | CLI + Textual TUI tools | — |

!!! note "Legacy packages"
    `ws-engine`, `ws-api`, `ws-client`, and `ws-cli` are retained for backward compatibility. New deployments should use the `resona-*` packages.

## The stateless engine contract

**resona-engine-server has no database and no persistent state.** Every request must be self-contained.

- `POST /transcribe` accepts `audio_file`, `language`, `task`, `initial_prompt`, `vad_filter`, `word_timestamps`
- The engine returns `{text, language, segments}` — raw transcript only
- **Replacements and postprocessing are not applied by the engine** — they are the caller's responsibility
- The engine never reads from or writes to a database
- The engine never deletes audio files — it receives bytes, returns JSON

This separation means:

- The engine can run on a remote GPU machine; the API runs anywhere CPU-only
- The engine can be swapped or scaled without touching the API
- Replacements and prompts live in resona-api's DB; the engine is a pure function
- Postprocessing (`resona-postprocess`) runs in resona-api after transcription completes

## Engine discovery via entry points

Engines are installed as separate packages and register themselves via Python entry points:

```toml
# In resona-engine-faster-whisper/pyproject.toml
[project.entry-points."resona.engines"]
faster-whisper = "resona_engine_faster_whisper.transcriber:FastWhisperTranscriber"
```

The `resona_asr_core.registry` discovers all installed engines at startup:

- `RESONA_ENGINE` env var selects which engine to load (default: `faster-whisper`)
- `get_transcriber()` returns a thread-safe singleton
- Each engine's `[project.scripts]` points to `resona_engine_server.run:main` — the same FastAPI app, different engine loaded

Available engine packages:

| Package | Entry point | Class | Notes |
|---------|-------------|-------|-------|
| `resona-engine-faster-whisper` | `faster-whisper` | `FastWhisperTranscriber` | CTranslate2, INT8, recommended |
| `resona-engine-whisper` | `whisper` | `WhisperTranscriber` | Original OpenAI Whisper (PyTorch) |
| `resona-engine-voxtral` | `voxtral` | `VoxtralTranscriber` | HuggingFace Transformers pipeline |

## Job lifecycle

```
Client
  │
  ▼  POST /jobs  (multipart: audio file)
resona-api
  ├── saves file to FILE_PATH
  ├── creates Job(status=PENDING) in SQLite
  └── returns job dict immediately

TranscribeTask (background thread, polls every 1s)
  ├── finds oldest PENDING job
  ├── sets status=PROCESSING
  ├── fetches active initial_prompt from DB
  ├── calls EngineClient.transcribe(filepath, language, prompt)
  │     └── POSTs multipart to resona-engine POST /transcribe
  │     └── engine loads audio, runs inference
  │     └── returns {text, language, segments}   ← raw, no replacements
  ├── fetches active replacements from DB
  ├── builds PostprocessPipeline (replacements + optional LLM steps)
  ├── md = pipeline.run(text)
  ├── writes md to Job row
  ├── writes .md file to MD_PATH
  └── sets status=COMPLETED (or FAILED on error)

Client
  └── GET /job/{id}  →  {status: "completed", transcript: "...", md: "..."}
```

## PostprocessPipeline

`resona-postprocess` provides a composable `str → str` pipeline:

```python
from resona_postprocess.pipeline import PostprocessPipeline
from resona_postprocess.replacements import ReplacementStep
from resona_postprocess.llm import LLMStep

pipeline = PostprocessPipeline([
    ReplacementStep(rules),          # regex replacements
    LLMStep(model="gpt-4o-mini"),    # optional LLM cleanup
])
result = pipeline.run(raw_text)
```

Pipeline configuration can be loaded from `~/.resona/postprocess.json` via `build_pipeline_from_config()`.

## ASR engines

The engine server selects its ASR engine via the `RESONA_ENGINE` environment variable:

| `RESONA_ENGINE` | Package | Class | Library | Notes |
|-----------------|---------|-------|---------|-------|
| `faster-whisper` (default) | `resona-engine-faster-whisper` | `FastWhisperTranscriber` | CTranslate2 | INT8 quantised, fast |
| `whisper` | `resona-engine-whisper` | `WhisperTranscriber` | openai-whisper | Original PyTorch |
| `voxtral` | `resona-engine-voxtral` | `VoxtralTranscriber` | transformers | HuggingFace pipeline |

The transcriber is instantiated once at startup and cached as a singleton.

## Cloud STT routing

`resona-cloud-stt` lets transcription be served by a cloud provider instead of a
local engine. It is httpx-only (no torch) and exposes three providers — Deepgram,
ElevenLabs, and OpenAI — each implementing `transcribe(audio_path, *, api_key,
model, language, options) -> {text, language, segments}`.

Two callers use it:

- **`resona transcribe`** — a cloud `EngineEntry` selected via `--engine` is wrapped
  by the CLI's `CloudEngine` and called directly.
- **`resona-api`** — when `RESONA_CLOUD_ENGINE` is set, `TranscribeTask` routes jobs
  through the cloud provider instead of `EngineClient`, then applies postprocessing
  as usual.

Provider API keys are read from environment variables (`DEEPGRAM_API_KEY`,
`ELEVENLABS_API_KEY`, `OPENAI_API_KEY`) at call time and are never persisted to
`config.json`.

## WebSocket endpoints

resona-engine-server exposes two WebSocket endpoints in addition to the HTTP endpoint:

### `WS /ws/transcribe`

Streaming transcription via `AudioBuffer`. The client sends base64-encoded 16 kHz PCM chunks; the engine accumulates 2 s windows with 0.5 s overlap and returns partial transcripts.

Protocol:
```json
// Client → Server
{"type": "audio", "data": "<base64 pcm>", "sample_rate": 16000}
{"type": "stop"}

// Server → Client
{"type": "transcript", "text": "...", "is_final": false}
{"type": "transcript", "text": "...", "is_final": true}
{"type": "stopped"}
{"type": "keepalive"}
{"type": "error", "message": "..."}
```

### `WS /ws/live`

VAD-based live transcription via `LiveTranscriber`. Uses `webrtcvad` to detect speech segments and transcribes each segment independently.

Protocol:
```json
// Client → Server
{"type": "audio_data", "data": "<base64 pcm>"}
{"type": "end_stream"}
{"type": "config", "language": "de", "task": "transcribe"}

// Server → Client
{"type": "transcript", "text": "...", "is_final": true, "segment_id": 1}
{"type": "interim", "text": "...", "is_final": false}
{"type": "stream_ended"}
{"type": "error", "message": "..."}
```

## Authentication

Both services support optional API key authentication via the `X-API-Key` request header.

- **resona-engine-server**: Set `RESONA_ENGINE_KEY`. If unset, all requests are allowed.
- **resona-api**: Set `RESONA_API_KEY`. If unset, all requests are allowed.

The API key is validated with `secrets.compare_digest` to prevent timing attacks.

## Data storage

All paths are configurable via environment variables:

```
DATA_PATH/
  files/    ← uploaded audio files (never auto-deleted)
  db/       ← resona.db SQLite database
  md/       ← generated Markdown transcripts
```

## Cross-package imports

```
resona-cli  ──imports──▶  resona_asr_core.live_transcriber  (live command, gated behind [live] extra)
resona-cli  ──imports──▶  resona_asr_core.registry           (InProcessEngine, gated behind engine extra)
resona-cli  ──imports──▶  resona_client.client               (all HTTP ops)
```

All other cross-service communication is over HTTP. Never import resona-api or resona-engine-server from resona-client.
