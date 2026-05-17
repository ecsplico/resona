# resona-engine-server

resona-engine-server is a stateless FastAPI service that wraps one ASR engine and exposes it over HTTP and WebSocket. It has no database, no persistent state, and applies no postprocessing. Every request is self-contained.

- Default port: **7001**
- Auth: optional `RESONA_ENGINE_KEY` env var
- Engine selection: `RESONA_ENGINE` env var (default: `faster-whisper`)

Each engine package (`resona-engine-faster-whisper`, `resona-engine-whisper`, `resona-engine-voxtral`) points its `[project.scripts]` entry at `resona_engine_server.run:main` — the same FastAPI app, loaded with a different backend.

## Authentication

Set `RESONA_ENGINE_KEY` on the server to require an API key. When set, every request must include `X-API-Key: <key>`. Comparison uses `secrets.compare_digest` to prevent timing attacks. If the env var is unset, all requests are accepted.

!!! note "Engine vs API key"
    `RESONA_ENGINE_KEY` guards the engine directly. `RESONA_API_KEY` guards resona-api. In a standard deployment only resona-api talks to the engine, so the engine key is an optional defence-in-depth measure.

## Endpoints

### Health check

```
GET /health
```

Returns liveness status plus the active engine name and loaded model.

```json
{
  "status": "ok",
  "engine": "faster-whisper",
  "models": ["large-v3"]
}
```

The model name is read from `DEFAULT_FASTWHISPER_MODEL` / `DEFAULT_WHISPER_MODEL` / `DEFAULT_VOXTRAL_MODEL` depending on the active engine.

---

### Transcribe (HTTP)

```
POST /transcribe
Content-Type: multipart/form-data
```

Transcribe a complete audio file and return the raw result. No replacements, no postprocessing — those are the caller's responsibility.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `audio_file` | file | required | Audio in any ffmpeg-supported format |
| `task` | string | `"transcribe"` | `"transcribe"` or `"translate"` (translate → English) |
| `language` | string | `"de"` | BCP-47 source language hint |
| `initial_prompt` | string | — | Vocabulary hint prepended to the context |
| `vad_filter` | bool | `false` | Use voice-activity detection to skip silence |
| `word_timestamps` | bool | `false` | Include per-word timestamps in `segments` |

Response:

```json
{
  "text": "Das ist ein Test.",
  "language": "de",
  "segments": [
    {"start": 0.0, "end": 2.1, "text": "Das ist ein Test."}
  ]
}
```

When `word_timestamps=true` each segment contains a `words` array:

```json
{
  "start": 0.0, "end": 2.1, "text": "Das ist ein Test.",
  "words": [
    {"word": "Das", "start": 0.0, "end": 0.3},
    {"word": "ist", "start": 0.3, "end": 0.5}
  ]
}
```

!!! note "No md field"
    The engine response never contains `md` or postprocessed text. That is resona-api's responsibility.

---

### Streaming transcription (WebSocket)

```
WS /ws/transcribe
```

Chunk-based streaming transcription. The client sends audio in 2-second overlapping windows; the server returns partial transcripts as each window is processed.

#### Client → Server messages

| `type` | Additional fields | Description |
|--------|------------------|-------------|
| `"audio"` | `data` (base64 PCM int16), `sample_rate` | Append audio to the buffer |
| `"stop"` | — | Flush remaining audio and end the session |

Audio must be raw **PCM int16**, mono, at **16 kHz**. Encode it to base64 before sending.

```json
{"type": "audio", "data": "AAAA...", "sample_rate": 16000}
```

#### Server → Client messages

| `type` | Additional fields | Description |
|--------|------------------|-------------|
| `"transcript"` | `text`, `is_final` | Partial (`is_final: false`) or final (`is_final: true`) text |
| `"stopped"` | — | Session fully stopped; all audio flushed |
| `"error"` | `message` | Error during transcription |
| `"keepalive"` | — | Sent every 10 s of client silence |

The server accumulates audio in a rolling buffer. When the buffer reaches 2 seconds of audio, it transcribes that window (with 0.5 s overlap to avoid cutting words) and sends a `transcript` message. On `"stop"`, any remaining buffered audio is transcribed as a final message.

---

### Live transcription (WebSocket)

```
WS /ws/live
```

VAD-based live transcription with partial/confirmed output. Uses `LiveTranscriber` from `resona-asr-core`, which detects speech boundaries and builds stable confirmed text incrementally.

#### Client → Server messages

| `type` | Additional fields | Description |
|--------|------------------|-------------|
| `"audio"` | `data` (base64 PCM int16), `sample_rate` | Stream audio continuously |
| `"stop"` | — | Flush remaining audio and close |
| `"config"` | `language` | Change transcription language at runtime |

#### Server → Client messages

| `type` | Additional fields | Description |
|--------|------------------|-------------|
| `"partial"` | `text`, `confirmed` | Current unstable hypothesis + stable confirmed portion |
| `"final"` | `text` | Confirmed stable text for a completed utterance |
| `"stopped"` | — | Session stopped; final confirmed text already sent |
| `"error"` | `message` | Error during processing |
| `"keepalive"` | — | Sent every 30 s of client silence |

The `confirmed` field in `"partial"` messages grows monotonically — once text appears in `confirmed` it will not change. Only `text` (the unstable hypothesis) is subject to revision.

```json
// Partial update — "das ist" is stable, hypothesis continues
{"type": "partial", "text": "das ist ein Test", "confirmed": "das ist"}

// Utterance boundary detected — full confirmed text emitted
{"type": "final", "text": "das ist ein Test."}
```

## CORS

The engine server sets `Access-Control-Allow-Origin` from the `CORS_ORIGINS` env var (default `*`). Restrict this in production if the engine is exposed beyond resona-api.

## Model pre-loading

On startup the engine server loads the ASR model before accepting requests (via FastAPI lifespan). If model loading fails (e.g. GPU out of memory), the server logs a warning and falls back to loading on the first request.
