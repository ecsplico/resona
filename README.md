# Resona

Modular audio transcription platform with pluggable ASR backends and composable postprocessing. Designed for German medical dictation but usable for any language. Built as a uv workspace monorepo.

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
          ┌────────────▼──┐   ┌───────▼──────────────┐
          │  resona-api   │   │ resona-engine-server  │
          │  :7000        │──▶│  + backend plugin     │
          │               │   │                       │
          │ POST /jobs    │   │ POST /transcribe      │
          │ GET  /jobs/   │   │ WS   /ws/transcribe   │
          │ GET  /job/id  │   │ WS   /ws/live         │
          │ CRUD replace  │   │                       │
          │ CRUD prompts  │   │ Stateless, no DB      │
          │               │   │ depends on resona-    │
          │ SQLite DB     │   │ asr-core              │
          │ Postprocessing│   └───────────────────────┘
          └───────────────┘
```

**resona-engine-server** is stateless -- no database, no side effects, no postprocessing. It owns all GPU-heavy inference. The lean ASR contracts (protocol, registry, audio loader, live transcriber) live in a separate package — `resona-asr-core` — so they can be reused without the FastAPI dependency tree. **resona-api** owns the job queue, SQLite database, and applies postprocessing (replacements + optional LLM) after getting raw text from the engine. This allows the engine to run on a dedicated GPU machine while the API runs elsewhere.

Backends are discovered via Python entry points (`resona.backends` group). Each backend is a separate package with its own Dockerfile.

## Packages

| Package | Port | Description |
|---------|------|-------------|
| `resona-asr-core` | -- | Lean ASR contracts: protocol, registry, audio, live transcriber |
| `resona-engine-server` | 7001 | FastAPI HTTP/WS server, hosts an ASR backend |
| `resona-engine-faster-whisper` | -- | CTranslate2 backend (default, recommended) |
| `resona-engine-whisper` | -- | Original OpenAI Whisper (PyTorch) backend |
| `resona-engine-voxtral` | -- | HuggingFace Transformers backend (Voxtral, Whisper, etc.) |
| `resona-postprocess` | -- | Composable pipeline: regex replacements + LLM via litellm |
| `resona-api` | 7000 | Job queue + SQLite + postprocessing, calls engine via HTTP |
| `resona-client` | -- | httpx client library for the resona-api REST interface |
| `resona-cli` | -- | CLI: `resona transcribe/watch/rec/live/ui/backends/replacements/prompts` |

## Quick start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- ffmpeg
- NVIDIA GPU with CUDA (for the engine)

### Local development

```bash
# Install all workspace packages
uv sync --all-packages --no-build-isolation-package openai-whisper

# Start the engine (GPU required, separate terminal)
uv run resona-engine-faster-whisper

# Start the API (CPU-only, separate terminal)
uv run resona-api

# Verify
curl http://localhost:7001/health
curl http://localhost:7000/health
```

### Docker (recommended for deployment)

```bash
cp .env.example .env   # set RESONA_API_KEY at minimum

docker compose -f docker-compose.resona.yml up -d

curl http://localhost:7001/health
curl http://localhost:7000/health
```

The engine container requires a GPU and is health-checked before the API starts.

### Local-only mode (no server)

If no server is reachable, the CLI automatically spawns a local engine. The CLI now uses an in-process engine when a backend extra is installed (e.g. `resona-cli[faster-whisper]`). This avoids the subprocess spawn from earlier versions. If the extra isn't installed, the CLI falls back to spawning a local engine subprocess as before.

```bash
# Transcribe files -- starts a local engine automatically
uv run resona transcribe ./recordings/ --output-dir ./transcripts/

# Or a single file / quoted glob
uv run resona transcribe recording.mp3
uv run resona transcribe "recordings/*.mp3"

# Use a different backend
uv run resona transcribe ./recordings/ --backend whisper

# Set a default backend so you don't need --backend every time
# Edit ~/.resona/config.json and set "default_backend": "whisper"
```

## CLI usage

```bash
# Transcribe a file, glob, or directory
resona transcribe ./recordings/ --output-dir ./out/ --language de
resona transcribe recording.mp3
resona transcribe "recordings/*.mp3"

# Watch a directory and auto-submit new files
resona watch ./inbox/ --recursive --poll-interval 2.0

# Record audio (terminal UI)
resona rec

# Live transcription (terminal UI, streams to engine via WebSocket)
resona live

# Record, transcribe, and display result (terminal UI)
resona ui

# Manage remote backends
resona backends add gpu-server http://gpu-machine:7000
resona backends add home http://localhost:7000 --ssh user@homeserver.com
resona backends list
resona backends test

# Manage text replacements (spoken -> written corrections)
resona replacements list
resona replacements add "Komma" ","
resona replacements delete 3

# Manage Whisper initial prompts (vocabulary hints)
resona prompts list
resona prompts add "Befund, Diagnose, Therapie"
resona prompts activate 2
```

## Text replacements

Replacements are regex patterns applied to transcribed text after inference. They convert spoken punctuation, formatting commands, and name corrections into the desired written form.

Default replacements are bundled and active out of the box (German dictation commands like "Komma" -> `,`, "Punkt" -> `.`, "Absatz" -> newline, medical section headings, etc.).

**Server mode:** resona-api stores replacements in SQLite and applies them via `PostprocessPipeline` after the engine returns raw text.

**Local mode:** The CLI reads from `~/.resona/replacements.json` (or falls back to bundled defaults). You can also configure a full pipeline with LLM steps in `~/.resona/postprocess.json`.

### Customizing replacements

Override the defaults by creating `~/.resona/replacements.json`:

```json
[
  {"name": "\\s*Komma", "replacement": ","},
  {"name": "\\s*Punkt", "replacement": "."},
  {"name": "\\s*Absatz", "replacement": "\n"},
  {"name": "Monique", "replacement": "Monic"}
]
```

### Adding LLM postprocessing

Create `~/.resona/postprocess.json` to chain replacements with LLM formatting:

```json
{
  "steps": [
    {
      "type": "replacements",
      "source": "replacements.json"
    },
    {
      "type": "llm",
      "name": "format-medical",
      "prompt": "Format this medical transcription with proper paragraphs and punctuation. Do not change the content.",
      "model": "ollama/llama3"
    }
  ]
}
```

LLM postprocessing uses [litellm](https://docs.litellm.ai/) -- supports OpenAI, Anthropic, Ollama, vLLM, and 100+ other providers. Set the model string and any required API keys (e.g. `OPENAI_API_KEY`).

## Backend selection

Three transcription backends are available:

| Backend | Command | Best for |
|---------|---------|----------|
| `faster-whisper` (default) | `resona-engine-faster-whisper` | Production use, fastest inference |
| `whisper` | `resona-engine-whisper` | Full OpenAI Whisper compatibility |
| `voxtral` | `resona-engine-voxtral` | HuggingFace models (Voxtral, etc.) |

Select via environment variable or CLI flag:

```bash
# Environment variable
RESONA_BACKEND=whisper uv run resona-engine-whisper

# CLI flag (local fallback mode)
resona transcribe ./audio/ --backend voxtral

# Default backend in config
# ~/.resona/config.json: {"default_backend": "voxtral", "backends": [...]}
```

### Install personas

| Persona | Command |
|---------|---------|
| HTTP client only | `uv tool install --from ./apps/resona-cli resona-cli` |
| Record + submit | `uv tool install --from ./apps/resona-cli 'resona-cli[record]'` |
| Live transcription | `uv tool install --from ./apps/resona-cli 'resona-cli[live,faster-whisper]'` |
| Fully local | `uv tool install --from ./apps/resona-cli 'resona-cli[faster-whisper]'` ⚠️ |

⚠️ The backend extras pull torch nightly; `uv tool install` may not inherit the workspace's `pytorch-nightly` index. Workaround: use `uv run resona` from inside the workspace, or `uv pip install --extra-index-url https://download.pytorch.org/whl/nightly/cu128 'resona-cli[faster-whisper]'`.

## Configuration

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RESONA_BACKEND` | `faster-whisper` | Backend to load |
| `RESONA_ENGINE_URL` | `http://localhost:7001` | Engine URL (used by API) |
| `RESONA_ENGINE_KEY` | _(unset)_ | Engine API key; auth disabled if unset |
| `RESONA_API_URL` | `http://localhost:7000` | API URL (used by client/CLI) |
| `RESONA_API_KEY` | _(unset)_ | API key; auth disabled if unset |
| `RESONA_LLM_MODEL` | `gpt-4o-mini` | Default LLM for postprocessing |
| `RESONA_LLM_API_BASE` | _(unset)_ | Custom LLM endpoint (e.g. Ollama) |
| `DEFAULT_FASTWHISPER_MODEL` | `large-v3` | faster-whisper model name |
| `DEFAULT_WHISPER_MODEL` | `large-v3` | OpenAI Whisper model name |
| `DEFAULT_VOXTRAL_MODEL` | `openai/whisper-large-v3` | HuggingFace model ID |
| `DATA_PATH` | `./data` | Root data directory (API) |
| `LOGLEVEL` | `info` | Log level |

### Config files

```
~/.resona/
├── config.json          # Remote backends, auto-start settings, default_backend
├── replacements.json    # Override default text replacement rules
└── postprocess.json     # Full pipeline config: replacements + LLM steps
```

## API reference

### Engine (:7001)

```
GET  /health
POST /transcribe          multipart: audio_file, task, language, initial_prompt, vad_filter, word_timestamps
WS   /ws/transcribe       WebSocket batch transcription
WS   /ws/live             WebSocket live transcription with VAD
```

`POST /transcribe` returns:
```json
{"text": "raw transcript", "language": "de", "segments": [{"start": 0.0, "end": 1.5, "text": "..."}]}
```

No `md` field, no replacements -- the engine is stateless.

### API (:7000)

All endpoints require `X-API-Key` header when `RESONA_API_KEY` is configured.

```
GET  /health
POST /jobs                multipart: audio_files[], keep, translate
POST /jobs/registerfile   body: filename
GET  /job/{id}
GET  /jobs/
GET  /replacements/
POST /replacements/       body: {name, replacement}
DELETE /replacements/{id}
GET  /prompts/
POST /prompts/            body: {phrase}
PUT  /prompts/{id}/activate
PUT  /prompts/{id}/deactivate
DELETE /prompts/{id}
```

Job lifecycle: `PENDING` -> `PROCESSING` -> `COMPLETED` | `FAILED`

## Data storage

Audio files are kept permanently (`keepfile=True`). Directory layout under `DATA_PATH`:

```
data/
  files/      # uploaded audio files (never auto-deleted)
  db/         # SQLite database
  md/         # generated markdown transcripts
```

## Development

```bash
# Run tests
uv run pytest                              # all packages
uv run pytest packages/api/tests/          # single package
uv run pytest -k test_transcribe           # single test

# Add a dependency to a package
uv add --package resona-api httpx

# Documentation (MkDocs Material)
uv run mkdocs serve     # dev server at :8000
uv run mkdocs build     # static site to site/
```

Note: `uv sync` alone only installs workspace root dev deps. Always use `uv sync --all-packages` to install all workspace members.

## License

Private.
