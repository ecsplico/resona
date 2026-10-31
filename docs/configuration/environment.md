# Environment Variables

All configuration is read with [`python-decouple`](https://github.com/HBNetwork/python-decouple): environment variables take precedence over values in a `.env` file. Copy `.env.example` to `.env` to get started.

!!! note "Exception: resona-client"
    `resona-client` uses `os.getenv()` for `RESONA_API_URL` and `RESONA_API_KEY` (the org-bound bearer token) because it has no `python-decouple` dependency. All other packages use `config()` from `python-decouple`.

## resona-engine-server (`:7001`)

| Variable | Default | Description |
|----------|---------|-------------|
| `RESONA_ENGINE` | `faster-whisper` | Engine to load per engine-server process: `faster-whisper`, `whisper`, or `voxtral` |
| `RESONA_ENGINE_KEY` | _(unset)_ | Optional API key for the engine; auth is disabled when not set |
| `LOGLEVEL` | `info` | Log level: `debug`, `info`, `warning`, `error` |

## resona-api (`:7000`)

| Variable | Default | Description |
|----------|---------|-------------|
| `RESONA_ENGINE_URLS` | `http://localhost:7001` | Comma-separated list of engine-server URLs; the API probes all and load-balances across reachable ones |
| `RESONA_DEFAULT_ENGINE` | _(first available)_ | Engine name to use for requests that do not specify one |
| `RESONA_ENGINE_KEY` | _(unset)_ | API key sent to engine-server in the `X-API-Key` header |
| `RESONA_BOOTSTRAP_ORGANIZATION_ID` | _(unset)_ | Organization that owns jobs already in an upgraded database. Required only when a pre-identity database with job rows is first migrated; startup refuses without it. Hub access uses org-scoped credentials issued by `resona-admin`, not a shared key |
| `DATA_PATH` | `./data` | Root directory for all persistent data (SQLite DB and expiring per-organization job files) |
| `RESONA_MAX_UPLOAD_BYTES` | `209715200` (200 MiB) | Maximum size of one upload or worker result; enforced while streaming, larger requests get `413` |
| `RESONA_PROFILES_DIR` | `<DATA_PATH>/profiles` | Directory where the bundled and file-backed profile sources are resolved for CLI and postprocessing; hosted profile revisions are stored in the database per organization and served by `GET /profiles` |
| `RESONA_SYNC_WAIT_SECONDS` | `60` | Seconds `POST /v1/audio/transcriptions` waits for a worker to finish the job; on timeout it answers `504` with the `job_id` while the job keeps running |
| `LOGLEVEL` | `info` | Log level: `debug`, `info`, `warning`, `error` |

## resona-client / resona CLI

| Variable | Default | Description |
|----------|---------|-------------|
| `RESONA_API_URL` | `http://localhost:7000` | resona-api base URL |
| `RESONA_API_KEY` | _(unset)_ | Org-bound token issued by `resona-admin credential issue`; sent as `Authorization: Bearer <token>` |

## resona-postprocess / resona-cli (local mode)

| Variable | Default | Description |
|----------|---------|-------------|
| `RESONA_LLM_MODEL` | _(unset)_ | Explicit local CLI mode only: model for `llm`/`extract` steps that set no `model`. There is no default model; the hub never reads this variable |
| `RESONA_LLM_API_BASE` | _(unset)_ | Explicit local CLI mode only: API base for legacy LLM steps (for example a local Ollama instance). A non-loopback base makes the step cloud, which private runs deny |
| `RESONA_PROFILES_DIR` | `~/.resona/profiles/` | Directory where the CLI looks for named profile files in `--local` runs |

## Cloud provider keys

Provider API keys are read from environment variables and are never stored in
`~/.resona/config.json`. A key never enables cloud processing by itself: cloud stages also need the
organization setting `cloud_enabled=true`, a job submitted with `--allow-cloud`, and a hub-approved
non-private worker. The CLI never calls a provider directly.

| Variable | Provider | Used for |
|----------|----------|----------|
| `DEEPGRAM_API_KEY` | Deepgram | Deepgram STT (`nova-3`) + TTS |
| `ELEVENLABS_API_KEY` | ElevenLabs | ElevenLabs STT (`scribe_v1`) + TTS |
| `OPENAI_API_KEY` | OpenAI | OpenAI Whisper API (`whisper-1`) + TTS (`tts-1`) |

## Model names

Override the default model loaded by each local engine:

| Variable | Default | Engine |
|----------|---------|--------|
| `DEFAULT_FASTWHISPER_MODEL` | `large-v3` | resona-engine-faster-whisper (CTranslate2) |
| `DEFAULT_WHISPER_MODEL` | `large-v3` | resona-engine-whisper (PyTorch) |
| `DEFAULT_VOXTRAL_MODEL` | `openai/whisper-large-v3` | resona-engine-voxtral (HuggingFace Transformers) |

## Docker and .env file

When using `docker compose -f docker-compose.resona.yml`, variables are loaded from the `.env` file in the project root via the `env_file` directive on the API service. Engine containers have their key variables hard-coded in `docker-compose.resona.yml` and can be overridden by adding them to `.env`.

```bash
cp .env.example .env
# Edit .env, then:
docker compose -f docker-compose.resona.yml --profile faster-whisper up -d
```

!!! tip "Credentials"
    Hub access uses org-scoped credentials. Issue one on the hub host with `resona-admin credential issue --organization <id> --role client`, then send `Authorization: Bearer <token>` with every request (REST also accepts the same token in `X-API-Key`). Tokens are shown once; the hub stores only their SHA-256 hash. There is no auth-disabled mode.

!!! tip "Custom LLM endpoint"
    To run postprocessing on a local Ollama instance in local CLI mode, set:
    ```bash
    RESONA_LLM_MODEL=ollama/llama3
    RESONA_LLM_API_BASE=http://localhost:11434
    ```
    The loopback endpoint keeps the step private. Any model supported by [litellm](https://docs.litellm.ai/docs/providers) works, but cloud models are denied unless the run allows cloud (`--allow-cloud` with `cloud_enabled=true`).
