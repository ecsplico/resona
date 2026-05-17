# Stateless Engine Contract

The engine server (`resona-engine-server`) is intentionally stateless. Understanding this constraint is the single most important thing to know before contributing to or extending Resona.

## The rule

!!! danger "resona-engine-server has no database and no persistent state"
    Every request to the engine must be completely self-contained. The engine never reads from or writes to a database, never applies text replacements or LLM postprocessing, and never knows about jobs, prompts, or users.

### What the engine CAN do

- Load audio from the multipart request body
- Run ASR inference against the loaded model
- Return `{text, language, segments}` — raw transcript only

### What the engine CANNOT do

- Read from or write to any database
- Apply text replacements or LLM postprocessing
- Delete audio files
- Know about jobs, initial prompts, or replacements stored elsewhere

## The design test

Before adding any feature to an engine or engine-server, apply this test:

!!! tip "The request-body test"
    **"Can this be done using only what is in the HTTP request?"**

    If yes — it may belong in the engine.
    If it requires a database lookup or a postprocessing step — it belongs in `resona-api` or `resona-postprocess`.

## Why this separation matters

**GPU machine vs API machine.** The engine runs on a GPU host where inference is fast. The API runs on a lightweight host (or serverless container) with no GPU requirement. Keeping the engine stateless means you can scale, swap, or restart the GPU host without touching the API or any stored data.

**Swappable engines.** Because all engines expose the same HTTP interface, `resona-api` can switch between `faster-whisper`, `whisper`, `voxtral`, or a cloud provider without any code changes. The `RESONA_ENGINE` environment variable selects which plugin loads; the rest of the system is unaffected.

**Testability.** A stateless HTTP service is trivially mockable. Tests for `resona-api` mock `EngineClient.transcribe`; they never need a real GPU.

## Engine entry-point discovery

Engines register themselves in their `pyproject.toml` using the `resona.engines` entry-point group:

```toml
[project.entry-points."resona.engines"]
faster-whisper = "resona_engine_faster_whisper.transcriber:FastWhisperTranscriber"
```

At startup, `resona_asr_core/registry.py` scans all installed packages for this group. The `RESONA_ENGINE` environment variable selects which entry to load (default: `faster-whisper`). `get_transcriber()` returns a thread-safe singleton for the lifetime of the process.

Available built-in engines:

| Name | Package | Backend |
|------|---------|---------|
| `faster-whisper` | `resona-engine-faster-whisper` | CTranslate2 (default) |
| `whisper` | `resona-engine-whisper` | OpenAI Whisper / PyTorch |
| `voxtral` | `resona-engine-voxtral` | HuggingFace Transformers |

Each engine's `[project.scripts]` entry points to the same `resona_engine_server.run:main` entrypoint — the same FastAPI application, with a different engine loaded.

## POST /transcribe

The synchronous transcription endpoint. All parameters come from the multipart form body; none are read from a database.

**Request** (`multipart/form-data`):

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `audio_file` | file | Yes | Audio file (any format ffmpeg can decode) |
| `language` | string | No | BCP-47 language tag (e.g. `de`, `en`) |
| `task` | string | No | `transcribe` (default) or `translate` |
| `initial_prompt` | string | No | Hint text to bias the model |
| `vad_filter` | bool | No | Enable voice-activity filtering |
| `word_timestamps` | bool | No | Include per-word timestamps in segments |

**Response** (`application/json`):

```json
{
  "text": "Der Patient klagt über Kopfschmerzen.",
  "language": "de",
  "segments": [
    {
      "start": 0.0,
      "end": 2.4,
      "text": "Der Patient klagt über Kopfschmerzen."
    }
  ]
}
```

The `text` field is always the raw transcript. No replacements have been applied.

## WebSocket endpoints

The engine also exposes two WebSocket endpoints for streaming scenarios.

### WS /ws/transcribe — windowed streaming

Audio is accumulated in an `AudioBuffer` (2-second windows with 0.5-second overlap). Useful when the client streams audio in real time and wants incremental results.

**Client → Server:**

```json
{"type": "audio", "data": "<base64 PCM>", "sample_rate": 16000}
{"type": "stop"}
```

**Server → Client:**

```json
{"type": "transcript", "text": "...", "is_final": false}
{"type": "transcript", "text": "...", "is_final": true}
```

### WS /ws/live — VAD-based live transcription

Audio is segmented by a voice-activity detector (`webrtcvad`). Each detected speech segment is transcribed independently via `LiveTranscriber` from `resona-asr-core`. Suitable for the `resona live` TUI.

**Client → Server:**

```json
{"type": "audio_data", "data": "<base64 PCM>"}
{"type": "config", "language": "de", "task": "transcribe"}
{"type": "end_stream"}
```

**Server → Client:**

```json
{"type": "interim", "text": "...", "is_final": false}
{"type": "transcript", "text": "...", "is_final": true, "segment_id": 1}
{"type": "stream_ended"}
```

## How to add a new engine

See [Adding an Engine](../development/adding-engine.md) for the full step-by-step guide. In brief:

1. Create `packages/engine-<name>/` with src-layout.
2. Implement `__init__(self, device: str, modelname: str | None = None)` and `transcribe(audio: np.ndarray, **kwargs) -> TranscriptionResult`.
3. Register the entry point in `pyproject.toml` under `[project.entry-points."resona.engines"]`.
4. Point `[project.scripts]` at `resona_engine_server.run:main`.
5. The engine must not touch the database — apply the request-body test before adding any parameter.
