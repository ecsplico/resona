# whisper-server

Audio transcription system built on OpenAI Whisper / faster-whisper, designed for German medical dictation. Structured as a uv workspace monorepo with independently deployable services.

## Architecture

```
                    ┌──────────────────────┐
                    │     ws-cli           │
                    │  batch / watch /     │
                    │  replacements        │
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

**ws-engine** is stateless — no database, no side effects. It owns all GPU-heavy inference. **ws-api** owns the job queue, SQLite database, and calls the engine over HTTP. This allows the engine to run on a dedicated GPU machine while the API runs elsewhere.

## Packages

| Package | Port | Description |
|---------|------|-------------|
| `ws-engine` | 7001 | Stateless FastAPI transcription engine (GPU required) |
| `ws-api` | 7000 | Async job queue + SQLite, calls engine via HTTP |
| `ws-client` | — | httpx client library for the ws-api REST interface |
| `ws-cli` | — | CLI: `ws-cli watch/batch/replacements/prompts` |
| `ws-recorder` | — | Textual TUI audio recorder (`ws-rec`) |
| `ws-live` | — | Live transcription TUI, connects to ws-engine directly |
| `ws-ui` | — | Record-and-transcribe TUI, uses ws-client + ws-recorder |

## Entry Points

| Command | Package | Description |
|---------|---------|-------------|
| `ws-engine` | ws-engine | Start transcription engine on :7001 |
| `ws-api` | ws-api | Start job API on :7000 |
| `ws-cli batch <dir>` | ws-cli | Transcribe all audio files in a directory |
| `ws-cli watch <dir>` | ws-cli | Watch directory and auto-submit new files |
| `ws-cli replacements` | ws-cli | Manage text replacement rules |
| `ws-cli prompts` | ws-cli | Manage initial prompt phrases |
| `ws-rec` | ws-recorder | Terminal audio recorder |
| `ws-ui` | ws-ui | GUI audio recorder + transcription |
| `ws-live` | ws-live | Live transcription TUI |

## Quick Start

### Local development

Prerequisites: Python 3.12+, [uv](https://docs.astral.sh/uv/), ffmpeg, CUDA GPU (for ws-engine).

```bash
# Install all workspace packages
uv sync

# Start the engine (GPU required, separate terminal)
uv run ws-engine

# Start the API (CPU-only, separate terminal)
uv run ws-api

# Verify
curl http://localhost:7001/health
curl http://localhost:7000/health
```

### Docker (recommended for deployment)

```bash
cp .env.example .env
# Edit .env — set WS_API_KEY at minimum

docker compose up -d

curl http://localhost:7001/health
curl http://localhost:7000/health
```

The engine container requires a GPU and is health-checked before the API starts.

## Environment Variables

### ws-engine (:7001)

| Variable | Default | Description |
|----------|---------|-------------|
| `ASR_MODE` | `faster-whisper` | Backend: `faster-whisper`, `whisper`, `transformer` |
| `DEFAULT_FASTWHISPER_MODEL` | `large-v3` | faster-whisper model size |
| `ENGINE_API_KEY` | _(unset)_ | Optional API key; auth disabled if not set |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins |
| `LOGLEVEL` | `info` | Log level |

### ws-api (:7000)

| Variable | Default | Description |
|----------|---------|-------------|
| `ENGINE_URL` | `http://localhost:7001` | URL of the ws-engine service |
| `WS_API_KEY` | _(unset)_ | API key for clients; auth disabled if not set |
| `DATA_PATH` | `./data` | Root data directory |
| `FILE_PATH` | `$DATA_PATH/files` | Audio file storage directory |
| `DB_PATH` | `$DATA_PATH/db` | SQLite database directory |
| `LOGLEVEL` | `info` | Log level |

### ws-client / ws-cli

| Variable | Default | Description |
|----------|---------|-------------|
| `WS_API_URL` | `http://localhost:7000` | ws-api base URL |
| `WS_API_KEY` | _(empty)_ | API key for ws-api |

## API Reference

### ws-engine (:7001)

```
GET  /health
POST /transcribe
WS   /ws/transcribe
WS   /ws/live
```

`POST /transcribe` — multipart form fields:

| Field | Type | Description |
|-------|------|-------------|
| `audio_file` | file | Audio file to transcribe |
| `task` | string | `transcribe` or `translate` (default: `transcribe`) |
| `language` | string | Language code (default: `de`) |
| `initial_prompt` | string | Whisper initial prompt |
| `replacements` | string | JSON array of `{"name": "<regex>", "replacement": "<text>"}` |
| `vad_filter` | bool | Enable voice activity detection |
| `word_timestamps` | bool | Include per-word timestamps |

Response:
```json
{
  "text": "raw transcript",
  "language": "de",
  "segments": [{"start": 0.0, "end": 1.5, "text": "..."}],
  "md": "transcript with replacements applied"
}
```

`md` is only present when `replacements` is supplied.

### ws-api (:7000)

All endpoints require `X-API-Key` header when `WS_API_KEY` is configured.

```
GET  /health
POST /jobs                    multipart: audio_files[], keep, translate
POST /jobs/registerfile       body: filename (re-queue existing file)
GET  /job/{id}
GET  /jobs/
GET  /replacements/
POST /replacements/           body: {name, replacement}
DELETE /replacements/{id}
GET  /prompts/
POST /prompts/                body: {phrase}
PUT  /prompts/{id}/activate
PUT  /prompts/{id}/deactivate
DELETE /prompts/{id}
```

Job status lifecycle: `PENDING` → `PROCESSING` → `COMPLETED` | `FAILED`

## CLI Usage

```bash
# Watch a directory and auto-submit new audio files
ws-cli watch ./inbox/ --recursive --poll-interval 2.0

# Transcribe all files in a directory
ws-cli batch ./recordings/ --output-dir ./out/

# Manage text replacements (spoken → written corrections)
ws-cli replacements list
ws-cli replacements add "Komma" ","
ws-cli replacements delete 3

# Manage Whisper initial prompts (domain vocabulary hints)
ws-cli prompts list
ws-cli prompts add "Befund, Diagnose, Therapie"
ws-cli prompts activate 2
ws-cli prompts deactivate 1
ws-cli prompts remove 4
```

## Text Replacements

Replacements are regex patterns applied to transcribed text after inference. ws-api fetches active replacements from its SQLite database and passes them to ws-engine as a JSON form field with each transcription request. ws-engine applies them in order (case-insensitive) and returns the result as `md`.

Example: pattern `Komma` → replacement `,` converts spoken punctuation markers to symbols.

## Data Storage

Audio files are kept by default (`keepfile=True`). Directory layout under `DATA_PATH`:

```
data/
  files/      ← uploaded audio files (never auto-deleted)
  db/         ← whisper.db SQLite database
  md/         ← generated markdown transcripts
```

## Development

### Running a single package

```bash
uv run --package ws-engine ws-engine
uv run --package ws-api ws-api
uv run --package ws-cli ws-cli -- --help
```

### Adding a dependency

```bash
uv add --package ws-api httpx
```

### Running tests

```bash
uv run pytest                          # all packages
uv run pytest packages/ws-api/tests/   # single package
```
