# CLAUDE.md — whisper-server development guide

## Project structure

```
whisper-server/
├── pyproject.toml          ← workspace root (no build-system, no deps)
├── uv.lock
├── docker-compose.yml
├── apps/
│   ├── cli/                ← ws-cli: typer CLI (watch, batch, replacements, prompts, rec/live/ui TUIs)
│   └── web/                ← browser UI (PWA dictaphone, live page) — plain HTML/JS, no build step yet
└── packages/
    ├── ws-engine/          ← stateless transcription, GPU, :7001
    ├── ws-api/             ← job queue + DB, CPU, :7000
    └── ws-client/          ← httpx client library
```

- `apps/` contains end-user applications (CLI tool, web front-end).
- `packages/` contains the services and libraries they depend on.
- Each Python package follows src-layout: `<root>/src/<module>/`.

## The stateless engine contract

**ws-engine has no database and no persistent state.** Every request must be self-contained.

- `POST /transcribe` accepts `initial_prompt` and `replacements` as form fields
- `replacements` is a JSON-serialised array: `[{"name": "<regex>", "replacement": "<text>"}]`
- The engine applies replacements and returns `md` in the response; `text` is always the raw transcript
- The engine never reads from or writes to a database
- The engine never deletes audio files — it receives bytes, returns JSON

When adding functionality to ws-engine, ask: "can this be done with only what's in the HTTP request?" If it requires a DB lookup, it belongs in ws-api.

## Package responsibilities

### ws-engine
- `app.py` — FastAPI app, `/health`, `POST /transcribe`, `WS /ws/transcribe`, `WS /ws/live`
- `transcriber_factory.py` — selects transcriber based on `ASR_MODE` env var
- `transcriber_fast_whisper.py` — faster-whisper backend (default)
- `transcriber_whisper.py` — openai-whisper backend
- `transcriber_transformer.py` / `transcriber_transformer2.py` — HuggingFace backends
- `live_transcriber.py` — WebSocket live VAD-based transcription
- `replacements.py` — `apply_replacements(text, list[dict])` — pure, stateless
- `utils.py` — `run_asr()` and `load_audio()` only
- `auth.py` — optional `ENGINE_API_KEY` auth

### ws-api
- `app.py` — FastAPI lifespan: creates DB, starts `TranscribeTask`, instantiates `EngineClient`
- `endpoints.py` — all REST routes: jobs, replacements, prompts
- `tasks_transcribe.py` — background thread: dequeues PENDING jobs, calls `EngineClient.transcribe()`
- `engine_client.py` — `EngineClient.transcribe()`: fetches replacements/prompt from DB, POSTs to engine
- `db/models.py` — `Job`, `Replacement`, `InitialPrompt` SQLModel tables
- `db/engine.py` — SQLite engine + `create_db_and_tables()`
- `db/utils.py` — `register_job()`, `get_active_replacements()`, `get_active_initial_prompts_string()`
- `paths.py` — `DATA_PATH`, `FILE_PATH`, `DB_PATH` resolved from env

### ws-client
- `client.py` — `WhisperClient`: all ws-api HTTP operations. Reads `WS_API_URL` / `WS_API_KEY` from env.

### ws-cli (lives in `apps/cli/`)
- `main.py` — typer app root
- `watch.py` — `watch` subcommand: polls directory, calls `client.submit_job()`
- `batch.py` — `batch` subcommand: submit all files + wait for results
- `replacements.py` — CRUD via `WhisperClient`
- `prompts.py` — CRUD via `WhisperClient`
- `micrec.py` — `RecordingSession` + `MicRecApp` Textual TUI base; `rec` subcommand entry point
- `recorder.tcss` — CSS for the recorder TUI
- `live_ui.py` — `WSLiveApp`: live transcription TUI extending `MicRecApp`
- `live.tcss` — CSS for the live TUI
- `ui.py` — `WSUIApp`: record-and-transcribe TUI (records, submits job, polls result); `ui` subcommand
- `css/ui.tcss` — CSS for the ui TUI

## Import conventions

Within a package, use relative imports:
```python
from .db.models import Job
from .engine_client import EngineClient
```

Cross-package imports: ws-cli imports `ws_engine.live_transcriber` (for `live` command). All other cross-package communication is over HTTP.

## How to add a new endpoint to ws-api

1. Add the route to `packages/ws-api/src/ws_api/endpoints.py` using the existing `router`
2. Add any new DB models to `db/models.py` and call `create_db_and_tables()` on startup (already done in `app.py`)
3. Add a corresponding method to `WhisperClient` in `packages/ws-client/src/ws_client/client.py`
4. Add a CLI subcommand if appropriate

## How to add a new transcription backend to ws-engine

1. Create `packages/ws-engine/src/ws_engine/transcriber_<name>.py` with a class that has `.transcribe(audio: np.ndarray, **kwargs) -> dict`
2. Return dict with keys: `text` (str), `language` (str), `segments` (list)
3. Register it in `transcriber_factory.py` under a new `ASR_MODE` value
4. The transcriber must not touch the database — `initial_prompt` comes in through `**kwargs`

## Job flow

```
Client → POST /jobs → ws-api saves file, registers PENDING job
ws-api TranscribeTask polls PENDING jobs →
  fetches replacements + initial_prompt from DB →
  calls EngineClient.transcribe(filepath, language, initial_prompt, replacements) →
    serialises replacements as JSON form field →
    POSTs multipart to ws-engine POST /transcribe →
  engine applies replacements, returns {text, md, segments} →
ws-api writes transcript to Job row, sets status COMPLETED
Client → GET /job/{id} → sees COMPLETED job with transcript
```

## Running in development

```bash
# Install all packages (--all-packages is required — plain uv sync only installs root dev deps)
# openai-whisper needs --no-build-isolation-package due to a pkg_resources build dep issue
uv sync --all-packages --no-build-isolation-package openai-whisper

# Run individual services
uv run ws-engine      # :7001, needs GPU
uv run ws-api         # :7000, needs engine running

# TUI tools
uv run ws-cli rec     # recorder TUI
uv run ws-cli live    # live transcription TUI
uv run ws-cli ui      # record + transcribe

# Documentation (dev server with hot-reload at http://127.0.0.1:8000)
uv run mkdocs serve

# Build static docs to site/
uv run mkdocs build
```

## Testing

Tests live in `<pkg>/tests/`. Run with:

```bash
uv run pytest                       # all
uv run pytest packages/ws-api/tests/  # one package
uv run pytest apps/cli/tests/       # cli tests
uv run pytest -k test_transcribe    # one test
```

Mocking strategy:
- ws-engine: mock the transcriber at `ws_engine.utils.get_transcriber`, not at model level
- ws-api: mock `EngineClient.transcribe` with `respx` (httpx mock) — never hit the real engine in unit tests
- ws-client: use `respx.mock` to intercept httpx calls
- ws-cli: use typer's `CliRunner` for command tests

Audio fixtures: keep small WAV files (1-2 seconds, 16kHz mono) in `<pkg>/tests/fixtures/`.

## Docker

Both services build from the workspace root as context so uv can resolve the workspace:

```dockerfile
COPY pyproject.toml uv.lock* ./
COPY packages/ws-engine/ ./packages/ws-engine/
RUN uv sync --package ws-engine --frozen --no-dev
```

The engine uses `nvidia/cuda:12.8.0-runtime-ubuntu24.04`. The API uses `python:3.12-slim`. Do not add GPU deps to ws-api's Dockerfile.

## Environment and configuration

All config is read with `python-decouple`'s `config()`. This reads from env vars first, then `.env` file. Never use `os.environ[]` directly — use `config("VAR_NAME", default=...)`.

## Known state

- The old `Dockerfile` and `Dockerfile.gpu` at the root are superseded by `packages/*/Dockerfile`. They can be removed.
- `uv sync` in production requires `--no-build-isolation-package openai-whisper` (pkg_resources build dep issue). PyTorch nightly index may also require `--index-strategy unsafe-best-match`.
- `apps/web/` is plain HTML/JS with no build step — Vite integration is a future task.

## What NOT to do

- Do not add database access to ws-engine
- Do not delete audio files after transcription (`keepfile=True` is the default and intent)
- Do not add `ScanInboxTask` back — inbox scanning is done by `ws-cli watch`
- Do not add a synchronous `/asr` endpoint to ws-api — ws-engine owns direct transcription
