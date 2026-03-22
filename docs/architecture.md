# Architecture

## Service overview

| Package | Port | Role | GPU |
|---------|------|------|-----|
| `ws-engine` | 7001 | Stateless transcription (inference) | Required |
| `ws-api` | 7000 | Job queue, SQLite DB, file storage | No |
| `ws-client` | — | Python client library | — |
| `ws-cli` (`apps/cli`) | — | CLI + Textual TUI tools | — |

## The stateless engine contract

**ws-engine has no database and no persistent state.** Every request must be self-contained.

- `POST /transcribe` accepts `initial_prompt` and `replacements` as form fields
- `replacements` is a JSON-serialised array: `[{"name": "<regex>", "replacement": "<text>"}]`
- The engine applies replacements and returns `md` in the response; `text` is always the raw transcript
- The engine never reads from or writes to a database
- The engine never deletes audio files — it receives bytes, returns JSON

This separation means:

- The engine can run on a remote GPU machine; the API runs anywhere CPU-only
- The engine can be swapped or scaled without touching the API
- Replacements and prompts live in ws-api's DB; the engine is a pure function

## Job lifecycle

```
Client
  │
  ▼  POST /jobs  (multipart: audio file)
ws-api
  ├── saves file to FILE_PATH
  ├── creates Job(status=PENDING) in SQLite
  └── returns job dict immediately

TranscribeTask (background thread, polls every 1s)
  ├── finds oldest PENDING job
  ├── sets status=PROCESSING
  ├── fetches active replacements from DB
  ├── fetches active initial_prompt from DB
  ├── calls EngineClient.transcribe(filepath, language, prompt, replacements)
  │     └── serialises replacements as JSON form field
  │     └── POSTs multipart to ws-engine POST /transcribe
  │     └── engine loads audio, runs inference, applies replacements
  │     └── returns {text, md, language, segments}
  ├── writes md to Job row
  ├── writes .md file to MD_PATH
  └── sets status=COMPLETED (or FAILED on error)

Client
  └── GET /job/{id}  →  {status: "completed", transcript: "...", md: "..."}
```

## ASR backends

The engine selects its backend via the `ASR_MODE` environment variable:

| `ASR_MODE` | Class | Library | Notes |
|-----------|-------|---------|-------|
| `faster-whisper` (default) | `FastWhisperTranscriber` | CTranslate2 | INT8 quantised, fast |
| `whisper` | `WhisperTranscriber` | openai-whisper | Original PyTorch |
| `transformer` | `TransformerTranscriber` | HuggingFace | Pipeline API, chunked |
| `whisper-tf` | `TransformerTranscriber` (v2) | HuggingFace | Alternate transformer backend |

The transcriber is instantiated once at startup (`transcriber_factory.py`) and cached as a singleton.

## WebSocket endpoints

ws-engine exposes two WebSocket endpoints in addition to the HTTP endpoint:

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

- **ws-engine**: Set `ENGINE_API_KEY`. If unset, all requests are allowed.
- **ws-api**: Set `WS_API_KEY`. If unset, all requests are allowed.

The API key is validated with `secrets.compare_digest` to prevent timing attacks.

## Data storage

All paths are configurable via environment variables:

```
DATA_PATH/
  files/    ← uploaded audio files (never auto-deleted, keepfile=True)
  db/       ← whisper.db SQLite database
  md/       ← generated Markdown transcripts
```

## Cross-package imports

```
ws-cli  ──imports──▶  ws-engine.live_transcriber  (live command)
ws-cli  ──imports──▶  ws-client.client             (all HTTP ops)
ws-ui   ──imports──▶  ws-cli.micrec.MicRecApp      (TUI base class)
```

All other cross-service communication is over HTTP. Never import ws-api or ws-engine from ws-client.
