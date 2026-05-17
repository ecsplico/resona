# CLAUDE.md — Resona development guide

## Project overview

Resona is a modular transcription platform with pluggable ASR engines and a composable postprocessing pipeline. Designed for German medical dictation but usable for any language.

## Project structure

```
resona/
├── pyproject.toml          ← workspace root
├── uv.lock
├── docker-compose.resona.yml
├── apps/
│   ├── resona-cli/         ← resona: typer CLI (watch, transcribe, profiles, rec/live/ui TUIs)
│   └── web/                ← browser UI (PWA dictaphone, live page) — plain HTML/JS
└── packages/
    ├── asr-core/           ← resona-asr-core: lean ASR contracts (protocol, registry, audio, live_transcriber). No FastAPI.
    ├── engine-server/      ← resona-engine-server: FastAPI HTTP/WS app, :7001. Depends on asr-core.
    ├── engine-faster-whisper/ ← resona-engine-faster-whisper: CTranslate2 engine (default)
    ├── engine-whisper/     ← resona-engine-whisper: OpenAI Whisper (PyTorch) engine
    ├── engine-voxtral/     ← resona-engine-voxtral: HuggingFace Transformers engine
    ├── cloud-stt/          ← resona-cloud-stt: cloud STT providers (Deepgram, ElevenLabs, OpenAI)
    ├── cloud-tts/          ← resona-cloud-tts: cloud TTS providers (OpenAI, ElevenLabs, Deepgram)
    ├── postprocess/        ← resona-postprocess: profile-based postprocessing pipeline (replacements + LLM + extract)
    ├── api/                ← resona-api: job queue + DB + postprocessing + unified STT/TTS API, :7000
    └── client/             ← resona-client: httpx client library
```

- `apps/` contains end-user applications (CLI tool, web front-end).
- `packages/` contains the services and libraries they depend on.
- Each Python package follows src-layout: `<root>/src/<module>/`.

## The stateless engine contract

**resona-engine-server has no database and no persistent state.** Every request must be self-contained.

- `POST /transcribe` accepts `audio_file`, `language`, `task`, `initial_prompt`, `vad_filter`, `word_timestamps`
- The engine returns `{text, language, segments}` — raw transcript only
- **No replacements or postprocessing in the engine** — that is the caller's responsibility
- The engine never reads from or writes to a database
- The engine never deletes audio files

When adding functionality to engine-server, ask: "can this be done with only what's in the HTTP request?" If it requires a DB lookup or postprocessing, it belongs in resona-api or resona-postprocess.

## Engine discovery via entry points

Engines register themselves in their `pyproject.toml`:

```toml
[project.entry-points."resona.engines"]
faster-whisper = "resona_engine_faster_whisper.transcriber:FastWhisperTranscriber"
```

The registry in `resona_asr_core/registry.py` discovers engines at runtime:
- `RESONA_ENGINE` env var selects which engine to load (default: `faster-whisper`)
- `get_transcriber()` returns a thread-safe singleton
- Each engine's `[project.scripts]` points to `resona_engine_server.run:main` — same FastAPI app, different engine

Available engines: `faster-whisper` (default), `whisper`, `voxtral`.

## Package responsibilities

### resona-asr-core
- `protocol.py` — `Transcriber` Protocol + `TranscriptionResult` TypedDict
- `registry.py` — entry-point discovery, singleton, device detection
- `audio.py` — `load_audio()`, `SAMPLE_RATE`
- `live_transcriber.py` — VAD-based live transcription engine (numpy only)

### resona-engine-server
- `app.py` — FastAPI app: `/health`, `POST /transcribe`, `WS /ws/transcribe`, `WS /ws/live`
  - `/health` returns `{status: "ok", engine: str, models: [str]}`
- `auth.py` — optional `RESONA_ENGINE_KEY` auth
- `ws_transcribe.py`, `ws_live.py` — WebSocket endpoint handlers
- `run.py` — uvicorn entry point

### resona-engine-faster-whisper
- `transcriber.py` — `FastWhisperTranscriber`: CTranslate2 engine (default, recommended)
- Configured via `DEFAULT_FASTWHISPER_MODEL` env var

### resona-engine-whisper
- `transcriber.py` — `WhisperTranscriber`: original OpenAI Whisper (PyTorch)
- Configured via `DEFAULT_WHISPER_MODEL` env var

### resona-engine-voxtral
- `transcriber.py` — `VoxtralTranscriber`: HuggingFace Transformers pipeline (supports Voxtral, Whisper, etc.)
- Configured via `DEFAULT_VOXTRAL_MODEL` env var (default: `openai/whisper-large-v3`)

### resona-cloud-stt
- `types.py` — `TranscriptionResult` TypedDict: `{text, language, segments}`
- `errors.py` — `CloudSTTError` (base), `MissingAPIKeyError` (env var not set), `ProviderHTTPError` (non-2xx response)
- `registry.py` — `PROVIDERS` (set), `PROVIDER_ENV_KEYS` (name → env var), `DEFAULT_MODELS` (name → model), `get_provider(name)` (returns provider module)
- `providers/deepgram.py` — POSTs raw audio to Deepgram `/v1/listen`; default model `nova-3`; key env var `DEEPGRAM_API_KEY`
- `providers/elevenlabs.py` — POSTs audio to ElevenLabs Speech-to-Text; default model `scribe_v1`; key env var `ELEVENLABS_API_KEY`
- `providers/openai.py` — POSTs audio to OpenAI Whisper API; default model `whisper-1`; key env var `OPENAI_API_KEY`

### resona-cloud-tts
- `types.py` — `SpeechResult` TypedDict: `{audio: bytes, content_type: str}`
- `errors.py` — `CloudTTSError` (base), `MissingAPIKeyError(env_var)` (env var not set), `ProviderHTTPError(provider, status_code, body)` (non-2xx response)
- `registry.py` — `PROVIDERS` (set), `PROVIDER_ENV_KEYS` (name → env var), `DEFAULT_MODELS` (name → model), `DEFAULT_VOICES` (name → voice), `CONTENT_TYPES` (name → MIME type), `get_provider(name)` (returns provider module)
- `providers/openai.py` — `synthesize(text, *, api_key, model, voice, response_format, options)` → POSTs to `https://api.openai.com/v1/audio/speech`; Bearer auth; default model `tts-1`; key env var `OPENAI_API_KEY`
- `providers/elevenlabs.py` — POSTs to `https://api.elevenlabs.io/v1/text-to-speech/{voice_id}`; `xi-api-key` header; key env var `ELEVENLABS_API_KEY`
- `providers/deepgram.py` — POSTs to `https://api.deepgram.com/v1/speak`; Token auth; voice overrides model; key env var `DEEPGRAM_API_KEY`

### resona-postprocess
- `replacements.py` — `apply_replacements(text, list[dict])` — regex-based, case-insensitive
- `llm.py` — `llm_transform(text, prompt, ...)` and `llm_extract(text, prompt, ...)` via litellm; `llm_postprocess` is a deprecated alias for `llm_transform`
- `pipeline.py` — `PostprocessPipeline`: composable step chain; `PostprocessResult{text, data}` carries both formatted text and structured extraction data; `build_pipeline(profile)` constructs a runnable pipeline from a `Profile`
- `profile.py` — `Profile` dataclass (name, description, initial_prompt, steps); `resolve_profile(ref, profiles_dir)` accepts a name, inline JSON string, file path, or `Profile`/dict; `list_profiles(dir)`, `bundled_default()`
- `profiles/default.json` — bundled default profile with German dictation replacements (Komma, Punkt, Absatz, medical headings, name corrections)
- `default_replacements.json` — bundled replacement rules used by the default profile

### resona-api
- `app.py` — FastAPI lifespan: creates DB, starts `TranscribeTask`, instantiates `EngineClient`; runs `migration.py` on startup to export any legacy DB tables to `default.json`
- `endpoints.py` — REST routes: jobs
- `profiles_routes.py` — CRUD routes for profile files: `GET /profiles/`, `GET /profiles/{name}`, `PUT /profiles/{name}`, `DELETE /profiles/{name}`
- `audio_routes.py` — OpenAI-compatible audio routes: `GET /v1/engines`, `POST /v1/audio/transcriptions`, `POST /v1/audio/speech`; accepts optional `profile` field
- `engine_registry.py` — multi-backend catalogue: probes local engine `/health` endpoints, checks cloud provider API keys, `EngineInfo` dataclass, `resolve(name)`, `run_stt(engine, audio, ...)`, `run_tts(engine, text, ...)`, error hierarchy
- `tasks_transcribe.py` — background thread: dequeues PENDING jobs, resolves the job's profile via `resolve_profile`, calls engine with `profile.initial_prompt_string()`, **applies profile pipeline** after engine returns
- `engine_client.py` — `EngineClient.transcribe()`: POSTs to engine (no replacements sent)
- `migration.py` — on startup, exports legacy `Replacement`/`InitialPrompt` DB rows to a `default.json` profile file in `RESONA_PROFILES_DIR`; idempotent
- `db/models.py` — `Job` SQLModel table; `Job` has `engine`, `profile`, `profile_config`, `structured` fields
- `db/engine.py` — SQLite engine + `create_db_and_tables()`
- `db/utils.py` — `register_job()`
- `formatting.py` — writes markdown output files
- `paths.py` — `DATA_PATH`, `FILE_PATH`, `DB_PATH`, `PROFILES_PATH` resolved from env
- `auth.py` — optional `RESONA_API_KEY` auth

### resona-client
- `client.py` — `ResonaClient`: all resona-api HTTP operations. Reads `RESONA_API_URL` / `RESONA_API_KEY`. Includes profile CRUD methods (`list_profiles`, `get_profile`, `push_profile`, `pull_profile`, `delete_profile`) and a `profile` argument on `submit_job` and `create_transcription`.
- `config.py` — `EngineConfig`: `~/.resona/config.json`, auto-start (SSH tunnel, docker compose), `default_engine`, `default_profile`, `default_private`; `EngineEntry`: per-entry `type` (`resona-api` or `cloud`), `provider`, `model`, `options`, `private`; `resolve_engine(private_only=False)` — walks priority-ordered entries, optionally skipping non-private ones

### resona-cli (lives in `apps/resona-cli/`)
- `main.py` — typer app root, `resona` command
- `watch.py` — `watch` subcommand: polls directory, calls `client.submit_job()`; accepts `--profile`
- `transcribe.py` — `transcribe` subcommand: accepts files, glob patterns, or directories; `--engine NAME` unified selector (built-in local engine, config.json server entry, or cloud entry); `--private`/`--no-private` to require a private engine; `--profile NAME` or inline JSON profile; submits to resona-api, calls cloud provider, or falls back to a local engine
- `engine.py` — `Engine` Protocol + `RemoteEngine` (HTTP) + `InProcessEngine` (direct asr-core call) + `CloudEngine` (wraps an `EngineEntry` of type `cloud`; calls `resona_cloud_stt` provider directly); used by transcribe.
- `local_engine.py` — `LocalEngine`: subprocess-based fallback for transcribe when InProcessEngine extras aren't installed.
- `engines.py` — `resona engines` CRUD subcommands; `engines add --type cloud --provider <name>` registers cloud entries
- `profiles.py` — `resona profiles` subcommand: `list`, `show`, `push`, `pull`, `delete` for server-side profile management
- `micrec.py` — `RecordingSession` + `MicRecApp` Textual TUI base; `rec` subcommand
- `live_ui.py` — `WSLiveApp`: live transcription TUI
- `ui.py` — `WSUIApp`: record-and-transcribe TUI

## Import conventions

Within a package, use relative imports:
```python
from .db.models import Job
from .engine_client import EngineClient
```

Cross-package imports: resona-cli imports `resona_asr_core.live_transcriber` (for the `live` command) and `resona_asr_core.registry` (for `InProcessEngine`). Both `resona-asr-core` and `resona-engine-faster-whisper` are base dependencies of resona-cli, so these imports always resolve. All other cross-package communication is over HTTP.

## How to add a new transcription engine

1. Create `packages/engine-<name>/` with src-layout
2. Implement a class with `transcribe(audio: np.ndarray, **kwargs) -> TranscriptionResult`
3. Constructor: `__init__(self, device: str, modelname: str | None = None)`
4. Register in pyproject.toml: `[project.entry-points."resona.engines"]`
5. Add `[tool.uv.sources]` with `resona-asr-core = { workspace = true }` and `resona-engine-server = { workspace = true }`
6. Set `[project.scripts]` to `resona_engine_server.run:main`
7. The engine must not touch the database

## How to add a new endpoint to resona-api

1. Add the route to `packages/api/src/resona_api/endpoints.py`
2. Add any new DB models to `db/models.py`
3. Add a corresponding method to `ResonaClient` in `packages/client/src/resona_client/client.py`
4. Add a CLI subcommand if appropriate

## Job flow

### Server path

```
Client → POST /jobs (profile=<name|inline JSON>) → resona-api saves file, registers PENDING job
resona-api TranscribeTask polls PENDING jobs →
  resolves profile via resolve_profile(job.profile or "default", PROFILES_PATH) →
  calls EngineClient.transcribe(filepath, language, profile.initial_prompt_string()) →
    POSTs multipart to engine POST /transcribe (no replacements) →
  engine returns {text, language, segments} →
  result = build_pipeline(profile).run(text) →
  stores result.text as job.md, result.data as job.structured →
  writes transcript + md to Job row, sets status COMPLETED
Client → GET /job/{id} → sees COMPLETED job with transcript + md
```

Profile may be: a profile name (resolved from RESONA_PROFILES_DIR), an inline JSON string, or omitted (uses the bundled `default` profile).

### Local fallback path

```
resona transcribe ./audio/ --engine voxtral --profile my-profile
  no server reachable →
  resolves engine: --engine flag → config.json default_engine → "faster-whisper"
  resolves profile: --profile flag → config.json default_profile → bundled "default"
  spawns: uv run resona-engine-voxtral on a free port
  waits for /health →
  POSTs each audio file to local engine →
  engine returns {text, language, segments} →
  result = build_pipeline(profile).run(text) →
  writes transcript to output file
```

## Postprocessing

Postprocessing is driven by **profiles**. A profile is a JSON file (or inline JSON string) that bundles an `initial_prompt` list and an ordered list of pipeline steps. Profiles replace the old DB-backed `Replacement`/`InitialPrompt` tables.

### Profile file format

```json
{
  "name": "my-profile",
  "description": "German medical dictation with LLM formatting",
  "initial_prompt": ["Befund", "Diagnose", "Medikation"],
  "steps": [
    {
      "type": "replacements",
      "rules": [
        {"pattern": "\\bKomma\\b", "replacement": ","},
        {"pattern": "\\bPunkt\\b",  "replacement": "."}
      ]
    },
    {
      "type": "llm",
      "name": "format",
      "prompt": "Format this medical dictation as a structured clinical note.",
      "model": "ollama/llama3"
    }
  ]
}
```

Step types:
- `replacements` — regex rules applied case-insensitively. Supply `rules` (inline array) or `source` (path to a JSON rules file).
- `llm` — sends text to a language model via litellm; returns transformed text.
- `extract` — sends text to a language model and returns structured JSON data stored in `job.structured`.

### Bundled default profile

`profiles/default.json` is bundled with `resona-postprocess`. It includes German dictation commands:

| Spoken | Written |
|--------|---------|
| Komma | , |
| Punkt | . |
| Absatz | (newline) |
| Kapitel | # (heading) |
| Klammer auf/zu | ( ) |

Plus medical section headings (Verlauf, Medikation, Psychopathologischer Befund, Procedere) and name corrections.

### Using profiles

Server-side: pass `profile=<name>` or `profile=<inline JSON>` when submitting a job via `POST /jobs` or `POST /v1/audio/transcriptions`. Named profiles are loaded from `RESONA_PROFILES_DIR` (default `<DATA_PATH>/profiles/`); the bundled `default` profile is used when none is specified.

CLI: `resona transcribe ./audio/ --profile my-profile` or `resona transcribe ./audio/ --profile '{"name":"x","steps":[...]}'`

Manage server-side profiles with `resona profiles list|show|push|pull|delete`.

### Env vars for LLM steps

| Variable | Default | Description |
|----------|---------|-------------|
| `RESONA_LLM_MODEL` | `gpt-4o-mini` | Default LLM model for `llm`/`extract` steps that do not specify `model` |
| `RESONA_LLM_API_BASE` | (unset) | Custom API base URL (e.g. local Ollama) |
| `RESONA_PROFILES_DIR` | `<DATA_PATH>/profiles` (server) / `~/.resona/profiles/` (CLI) | Directory where named profile files are stored |

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
uv run resona transcribe ./audio/ --engine whisper --language en
```

### Editable vs. copied installs

`uv sync --all-packages` installs every workspace package into the workspace
`.venv` editable — `uv run resona <cmd>` from the repo root picks up source
edits to any package immediately. **This is the dev loop.**

`uv tool install` (Install personas below) **copies** the packages into an
isolated tool env; that copy is not editable. After editing code, an installed
tool must be refreshed with
`uv tool install --reinstall --from ./apps/resona-cli resona-cli`. Note that
`--editable` would only make `resona-cli` itself editable, not its workspace
dependencies (`resona-postprocess`, `resona-asr-core`, …) — so `uv run` from the
workspace is the only fully-editable path. Reserve `uv tool install` for testing
the end-user personas.

### Install personas

| Persona | Command |
|---------|---------|
| Default (record, live, local faster-whisper) | `uv tool install --from ./apps/resona-cli resona-cli` |
| Default + Whisper (PyTorch) engine | `uv tool install --from ./apps/resona-cli 'resona-cli[whisper]'` |
| Default + Voxtral (PyTorch) engine | `uv tool install --from ./apps/resona-cli 'resona-cli[voxtral]'` |

See [docs/getting-started/installation.md](docs/getting-started/installation.md) for details including PyTorch extras.

```bash
# Documentation
uv run mkdocs serve                    # dev server at :8000
uv run mkdocs build                    # static docs to site/
```

## Testing

Tests live in `<pkg>/tests/`. Run with:

```bash
uv run pytest                                      # all tests
uv run pytest packages/engine-server/tests/        # engine server
uv run pytest packages/asr-core/tests/             # asr core
uv run pytest packages/api/tests/                  # api
uv run pytest packages/client/tests/               # client
uv run pytest apps/resona-cli/tests/               # cli
uv run pytest packages/postprocess/tests/          # postprocess
uv run pytest -k test_transcribe                   # one test
```

Mocking strategy:
- resona-engine-server: mock the transcriber at `resona_engine_server.app.get_transcriber`
- resona-api: mock `EngineClient.transcribe` with `respx` (httpx mock)
- resona-client: use `respx.mock` to intercept httpx calls
- resona-cli: use typer's `CliRunner` for command tests

Audio fixtures: keep small WAV files (1-2 seconds, 16kHz mono) in `<pkg>/tests/fixtures/`.

## Docker

Each engine builds from the workspace root as context:

```dockerfile
COPY pyproject.toml uv.lock* ./
COPY packages/engine-server/ ./packages/engine-server/
COPY packages/asr-core/ ./packages/asr-core/
COPY packages/engine-faster-whisper/ ./packages/engine-faster-whisper/
RUN uv sync --package resona-engine-faster-whisper --frozen --no-dev
```

Engine packages use `nvidia/cuda:12.8.0-runtime-ubuntu24.04`. The API uses `python:3.12-slim`. Do not add GPU deps to the API Dockerfile.

Run with: `docker compose -f docker-compose.resona.yml up`

## Environment and configuration

All config is read with `python-decouple`'s `config()`. This reads from env vars first, then `.env` file. Never use `os.environ[]` directly — use `config("VAR_NAME", default=...)`.

Exception: `resona-client` uses `os.getenv()` for `RESONA_API_URL` / `RESONA_API_KEY` (it has no decouple dependency).

### Key environment variables

See [docs/configuration/environment.md](docs/configuration/environment.md) for the full env var reference.

### Engine resolution order (`resona transcribe`)

1. `--engine NAME` CLI flag: resolves a built-in local engine name (`faster-whisper`, `whisper`, `voxtral`), a `config.json` server entry, or a `config.json` cloud entry (highest priority)
2. `--private` / `--no-private`: when private is required (via flag or `default_private`), non-private engines are skipped or refused; cloud engines are never private
3. `default_engine` in `~/.resona/config.json`
4. Hardcoded default: `"faster-whisper"`

## What NOT to do

- Do not add database access to engine-server or any engine package
- Do not add postprocessing (replacements, LLM) to the engine — it belongs in resona-api or resona-postprocess
- Do not delete audio files after transcription
- Do not add `ScanInboxTask` back — inbox scanning is done by `resona watch`
- Do not add a synchronous `/asr` endpoint to resona-api — the engine owns direct transcription
- Do not use `os.environ[]` — use `config()` from python-decouple
