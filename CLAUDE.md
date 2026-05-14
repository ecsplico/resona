# CLAUDE.md — Resona development guide

## Project overview

Resona is a modular transcription platform with pluggable ASR backends and a composable postprocessing pipeline. Designed for German medical dictation but usable for any language.

## Project structure

```
resona/
├── pyproject.toml          ← workspace root
├── uv.lock
├── docker-compose.resona.yml
├── apps/
│   ├── resona-cli/         ← resona: typer CLI (watch, transcribe, replacements, prompts, rec/live/ui TUIs)
│   └── web/                ← browser UI (PWA dictaphone, live page) — plain HTML/JS
└── packages/
    ├── engine-core/        ← resona-engine-core: FastAPI app, Transcriber protocol, registry, :7001
    ├── engine-faster-whisper/ ← resona-engine-faster-whisper: CTranslate2 backend (default)
    ├── engine-whisper/     ← resona-engine-whisper: OpenAI Whisper (PyTorch) backend
    ├── engine-voxtral/     ← resona-engine-voxtral: HuggingFace Transformers backend
    ├── postprocess/        ← resona-postprocess: replacements + LLM pipeline
    ├── api/                ← resona-api: job queue + DB + postprocessing, :7000
    └── client/             ← resona-client: httpx client library
```

- `apps/` contains end-user applications (CLI tool, web front-end).
- `packages/` contains the services and libraries they depend on.
- Each Python package follows src-layout: `<root>/src/<module>/`.

## The stateless engine contract

**resona-engine-core has no database and no persistent state.** Every request must be self-contained.

- `POST /transcribe` accepts `audio_file`, `language`, `task`, `initial_prompt`, `vad_filter`, `word_timestamps`
- The engine returns `{text, language, segments}` — raw transcript only
- **No replacements or postprocessing in the engine** — that is the caller's responsibility
- The engine never reads from or writes to a database
- The engine never deletes audio files

When adding functionality to engine-core, ask: "can this be done with only what's in the HTTP request?" If it requires a DB lookup or postprocessing, it belongs in resona-api or resona-postprocess.

## Backend discovery via entry points

Backends register themselves in their `pyproject.toml`:

```toml
[project.entry-points."resona.backends"]
faster-whisper = "resona_engine_faster_whisper.transcriber:FastWhisperTranscriber"
```

The registry in `resona_engine_core/registry.py` discovers backends at runtime:
- `RESONA_BACKEND` env var selects which backend to load (default: `faster-whisper`)
- `get_transcriber()` returns a thread-safe singleton
- Each backend's `[project.scripts]` points to `resona_engine_core.run:main` — same FastAPI app, different backend

Available backends: `faster-whisper` (default), `whisper`, `voxtral`.

## Package responsibilities

### resona-engine-core
- `protocol.py` — `Transcriber` Protocol + `TranscriptionResult` TypedDict
- `registry.py` — entry-point discovery, singleton, device detection
- `app.py` — FastAPI app: `/health`, `POST /transcribe`, `WS /ws/transcribe`, `WS /ws/live`
- `audio.py` — `load_audio()`, `SAMPLE_RATE`
- `auth.py` — optional `RESONA_ENGINE_KEY` auth
- `live_transcriber.py` — WebSocket live VAD-based transcription
- `ws_transcribe.py`, `ws_live.py` — WebSocket endpoint handlers
- `run.py` — uvicorn entry point

### resona-engine-faster-whisper
- `transcriber.py` — `FastWhisperTranscriber`: CTranslate2 backend (default, recommended)
- Configured via `DEFAULT_FASTWHISPER_MODEL` env var

### resona-engine-whisper
- `transcriber.py` — `WhisperTranscriber`: original OpenAI Whisper (PyTorch)
- Configured via `DEFAULT_WHISPER_MODEL` env var

### resona-engine-voxtral
- `transcriber.py` — `VoxtralTranscriber`: HuggingFace Transformers pipeline (supports Voxtral, Whisper, etc.)
- Configured via `DEFAULT_VOXTRAL_MODEL` env var (default: `openai/whisper-large-v3`)

### resona-postprocess
- `replacements.py` — `apply_replacements(text, list[dict])` — regex-based, case-insensitive
- `llm.py` — `llm_postprocess(text, prompt, model, api_base)` via litellm
- `pipeline.py` — `PostprocessPipeline`: composable `str → str` chain
- `sources.py` — `build_pipeline_from_config()` reads `~/.resona/postprocess.json`, falls back to bundled defaults
- `default_replacements.json` — bundled German dictation replacements (Komma, Punkt, Absatz, medical headings, name corrections)

### resona-api
- `app.py` — FastAPI lifespan: creates DB, starts `TranscribeTask`, instantiates `EngineClient`
- `endpoints.py` — REST routes: jobs, replacements, prompts
- `tasks_transcribe.py` — background thread: dequeues PENDING jobs, calls engine, **applies postprocessing locally**
- `engine_client.py` — `EngineClient.transcribe()`: POSTs to engine (no replacements sent)
- `db/models.py` — `Job`, `Replacement`, `InitialPrompt` SQLModel tables
- `db/engine.py` — SQLite engine + `create_db_and_tables()`
- `db/utils.py` — `register_job()`, `get_active_replacements()`, `get_active_initial_prompts_string()`
- `db/presets.py` — default replacements and initial prompts seeded on first DB creation
- `formatting.py` — writes markdown output files
- `paths.py` — `DATA_PATH`, `FILE_PATH`, `DB_PATH` resolved from env
- `auth.py` — optional `RESONA_API_KEY` auth

### resona-client
- `client.py` — `ResonaClient`: all resona-api HTTP operations. Reads `RESONA_API_URL` / `RESONA_API_KEY`.
- `config.py` — `BackendConfig`: `~/.resona/config.json`, auto-start (SSH tunnel, docker compose), `default_backend`

### resona-cli (lives in `apps/resona-cli/`)
- `main.py` — typer app root, `resona` command
- `watch.py` — `watch` subcommand: polls directory, calls `client.submit_job()`
- `transcribe.py` — `transcribe` subcommand: accepts files, glob patterns, or directories; submits + waits for results
- `local_engine.py` — `LocalEngine`: spawns `uv run resona-engine-{backend}` as fallback
- `backends.py`, `replacements.py`, `prompts.py` — CRUD subcommands
- `micrec.py` — `RecordingSession` + `MicRecApp` Textual TUI base; `rec` subcommand
- `live_ui.py` — `WSLiveApp`: live transcription TUI
- `ui.py` — `WSUIApp`: record-and-transcribe TUI

## Import conventions

Within a package, use relative imports:
```python
from .db.models import Job
from .engine_client import EngineClient
```

Cross-package imports: resona-cli imports `resona_engine_core.live_transcriber` (for `live` command). All other cross-package communication is over HTTP.

## How to add a new transcription backend

1. Create `packages/engine-<name>/` with src-layout
2. Implement a class with `transcribe(audio: np.ndarray, **kwargs) -> TranscriptionResult`
3. Constructor: `__init__(self, device: str, modelname: str | None = None)`
4. Register in pyproject.toml: `[project.entry-points."resona.backends"]`
5. Add `[tool.uv.sources]` with `resona-engine-core = { workspace = true }`
6. Set `[project.scripts]` to `resona_engine_core.run:main`
7. The backend must not touch the database

## How to add a new endpoint to resona-api

1. Add the route to `packages/api/src/resona_api/endpoints.py`
2. Add any new DB models to `db/models.py`
3. Add a corresponding method to `ResonaClient` in `packages/client/src/resona_client/client.py`
4. Add a CLI subcommand if appropriate

## Job flow

### Server path

```
Client → POST /jobs → resona-api saves file, registers PENDING job
resona-api TranscribeTask polls PENDING jobs →
  fetches initial_prompt from DB →
  calls EngineClient.transcribe(filepath, language, initial_prompt) →
    POSTs multipart to engine POST /transcribe (no replacements) →
  engine returns {text, language, segments} →
  resona-api builds PostprocessPipeline from DB replacements →
  md = pipeline.run(text) →
  writes transcript + md to Job row, sets status COMPLETED
Client → GET /job/{id} → sees COMPLETED job with transcript + md
```

### Local fallback path

```
resona transcribe ./audio/ --backend voxtral
  no server reachable →
  resolves backend: --backend flag → config.json default_backend → "faster-whisper"
  spawns: uv run resona-engine-voxtral on a free port
  waits for /health →
  POSTs each audio file to local engine →
  engine returns {text, language, segments} →
  builds PostprocessPipeline from ~/.resona/postprocess.json (or bundled defaults) →
  md = pipeline.run(text) →
  writes transcript to output file
```

## Postprocessing

Postprocessing is a composable pipeline of `str → str` steps applied **after** the engine returns raw text.

### Default replacements

Bundled in `resona_postprocess/default_replacements.json` and active out of the box. Includes German dictation commands:

| Spoken | Written |
|--------|---------|
| Komma | , |
| Punkt | . |
| Absatz | (newline) |
| Kapitel | # (heading) |
| Klammer auf/zu | ( ) |

Plus medical section headings (Verlauf, Medikation, Psychopathologischer Befund, Procedere) and name corrections.

### Customizing

Override by creating `~/.resona/replacements.json`. Or for a full pipeline with LLM steps, create `~/.resona/postprocess.json`:

```json
{
  "steps": [
    {"type": "replacements", "source": "replacements.json"},
    {"type": "llm", "name": "format", "prompt": "Format this medical text.", "model": "ollama/llama3"}
  ]
}
```

Relative paths in `source` resolve relative to the config directory (`~/.resona/`).

## Running in development

```bash
# Install all packages
uv sync --all-packages --no-build-isolation-package openai-whisper

# Run individual services
uv run resona-engine-faster-whisper   # :7001, needs GPU
uv run resona-api                      # :7000, needs engine running

# CLI tools
uv run resona rec                      # recorder TUI
uv run resona live                     # live transcription TUI
uv run resona ui                       # record + transcribe
uv run resona transcribe ./audio/      # transcribe a directory
uv run resona transcribe one.mp3       # transcribe a single file
uv run resona transcribe "audio/*.mp3" # transcribe a quoted glob
uv run resona watch ./inbox/           # watch directory

# Local-only (no server needed — spawns engine automatically)
uv run resona transcribe ./audio/ --output-dir ./out/
uv run resona transcribe ./audio/ --backend whisper --language en

# Documentation
uv run mkdocs serve                    # dev server at :8000
uv run mkdocs build                    # static docs to site/
```

## Testing

Tests live in `<pkg>/tests/`. Run with:

```bash
uv run pytest                                    # all (238 tests)
uv run pytest packages/engine-core/tests/        # engine core
uv run pytest packages/api/tests/                # api
uv run pytest packages/client/tests/             # client
uv run pytest apps/resona-cli/tests/             # cli
uv run pytest packages/postprocess/tests/        # postprocess
uv run pytest -k test_transcribe                 # one test
```

Mocking strategy:
- resona-engine-core: mock the transcriber at `resona_engine_core.app.get_transcriber`
- resona-api: mock `EngineClient.transcribe` with `respx` (httpx mock)
- resona-client: use `respx.mock` to intercept httpx calls
- resona-cli: use typer's `CliRunner` for command tests

Audio fixtures: keep small WAV files (1-2 seconds, 16kHz mono) in `<pkg>/tests/fixtures/`.

## Docker

Each backend builds from the workspace root as context:

```dockerfile
COPY pyproject.toml uv.lock* ./
COPY packages/engine-core/ ./packages/engine-core/
COPY packages/engine-faster-whisper/ ./packages/engine-faster-whisper/
RUN uv sync --package resona-engine-faster-whisper --frozen --no-dev
```

Engine backends use `nvidia/cuda:12.8.0-runtime-ubuntu24.04`. The API uses `python:3.12-slim`. Do not add GPU deps to the API Dockerfile.

Run with: `docker compose -f docker-compose.resona.yml up`

## Environment and configuration

All config is read with `python-decouple`'s `config()`. This reads from env vars first, then `.env` file. Never use `os.environ[]` directly — use `config("VAR_NAME", default=...)`.

Exception: `resona-client` uses `os.getenv()` for `RESONA_API_URL` / `RESONA_API_KEY` (it has no decouple dependency).

### Key environment variables

| Variable | Package | Purpose | Default |
|----------|---------|---------|---------|
| `RESONA_BACKEND` | engine-core | Backend selection | `faster-whisper` |
| `RESONA_ENGINE_URL` | api | Engine service URL | `http://localhost:7001` |
| `RESONA_ENGINE_KEY` | engine-core | Engine auth key | (none, auth disabled) |
| `RESONA_API_URL` | client | API server URL | `http://localhost:7000` |
| `RESONA_API_KEY` | api, client | API auth key | (none, auth disabled) |
| `RESONA_LLM_MODEL` | postprocess | Default LLM model | `gpt-4o-mini` |
| `RESONA_LLM_API_BASE` | postprocess | Custom LLM endpoint | (none) |
| `DEFAULT_FASTWHISPER_MODEL` | engine-faster-whisper | Model name | `large-v3` |
| `DEFAULT_WHISPER_MODEL` | engine-whisper | Model name | `large-v3` |
| `DEFAULT_VOXTRAL_MODEL` | engine-voxtral | HuggingFace model ID | `openai/whisper-large-v3` |
| `DATA_PATH` | api | Root data directory | `./data` |

### Config files

```
~/.resona/
├── config.json          ← remote backends, auto-start settings, default_backend
├── replacements.json    ← override default text replacement rules
└── postprocess.json     ← full pipeline config: replacements + LLM steps
```

If neither `postprocess.json` nor `replacements.json` exists, bundled default replacements are used automatically.

### Backend resolution order (local fallback)

1. `--backend` CLI flag (highest priority)
2. `default_backend` in `~/.resona/config.json`
3. Hardcoded default: `"faster-whisper"`

## What NOT to do

- Do not add database access to engine-core or any engine backend
- Do not add postprocessing (replacements, LLM) to the engine — it belongs in resona-api or resona-postprocess
- Do not delete audio files after transcription
- Do not add `ScanInboxTask` back — inbox scanning is done by `resona watch`
- Do not add a synchronous `/asr` endpoint to resona-api — the engine owns direct transcription
- Do not use `os.environ[]` — use `config()` from python-decouple
