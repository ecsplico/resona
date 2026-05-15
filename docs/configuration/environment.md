# Environment Variables

All configuration is read with `python-decouple`: environment variables take precedence over values in `.env`. Copy `.env.example` to `.env` to get started.

## resona-engine-server (`:7001`)

| Variable | Default | Description |
|----------|---------|-------------|
| `RESONA_ENGINE` | `faster-whisper` | Engine to load: `faster-whisper`, `whisper`, or any installed entry-point name |
| `DEFAULT_FASTWHISPER_MODEL` | `large-v3` | Model name/path for faster-whisper backend |
| `DEFAULT_WHISPER_MODEL` | `large-v3` | Model name/path for openai-whisper backend |
| `DEFAULT_TRANSFORMER_MODEL` | — | HuggingFace model ID for transformer backends |
| `RESONA_ENGINE_KEY` | _(unset)_ | Optional API key for the engine; auth disabled if not set |
| `CORS_ORIGINS` | `*` | Comma-separated allowed CORS origins |
| `LOGLEVEL` | `info` | Log level (`debug`, `info`, `warning`, `error`) |

## resona-api (`:7000`)

| Variable | Default | Description |
|----------|---------|-------------|
| `RESONA_ENGINE_URL` | `http://localhost:7001` | URL of the resona-engine service |
| `RESONA_ENGINE_KEY` | _(unset)_ | API key sent to the engine (`X-API-Key`) |
| `RESONA_API_KEY` | _(unset)_ | API key required from clients; auth disabled if not set |
| `DATA_PATH` | `./data` | Root directory for all data |
| `FILE_PATH` | `$DATA_PATH/files` | Audio file storage directory |
| `DB_PATH` | `$DATA_PATH/db` | SQLite database directory |
| `LOGLEVEL` | `info` | Log level |

## resona-postprocess

| Variable | Default | Description |
|----------|---------|-------------|
| `RESONA_LLM_MODEL` | `gpt-4o-mini` | Default LLM model for postprocessing steps |
| `RESONA_LLM_API_BASE` | _(unset)_ | Custom LLM endpoint (e.g. local Ollama); uses litellm |

## resona-client / resona CLI

| Variable | Default | Description |
|----------|---------|-------------|
| `RESONA_API_URL` | `http://localhost:7000` | resona-api base URL (overrides `~/.resona/config.json`) |
| `RESONA_API_KEY` | _(empty)_ | API key for resona-api (`X-API-Key` header) |
| `FILE_PATH` | `./data/files` | Output directory for recorded audio |
| `SAMPLE_RATE` | `44100` | Microphone sample rate (Hz) |
| `CHANNELS` | `1` | Microphone channel count |
| `MD_PATH` | `./data/md` | Directory for saved Markdown transcripts |

## Docker Compose

When using `docker compose -f docker-compose.resona.yml`, variables are loaded from the `.env` file in the project root. The engine variables can also be set inline in `docker-compose.resona.yml`.

```bash
cp .env.example .env
# Edit .env
docker compose -f docker-compose.resona.yml up -d
```

!!! tip "API key setup"
    If you set `RESONA_API_KEY` in `.env`, all clients must send `X-API-Key: <key>` with every request. Set the same value in `RESONA_API_KEY` for the `resona` CLI (or export it in your shell).

---

## Legacy variables (ws-* packages)

The following variables are used by the legacy `ws-engine`, `ws-api`, `ws-client`, and `ws-cli` packages. They are retained for backward compatibility.

### ws-engine (`:7001`, legacy)

| Variable | Default | Description |
|----------|---------|-------------|
| `ASR_MODE` | `faster-whisper` | Backend: `faster-whisper`, `whisper`, `transformer`, `whisper-tf` |
| `ENGINE_API_KEY` | _(unset)_ | Optional API key for ws-engine |

### ws-api (`:7000`, legacy)

| Variable | Default | Description |
|----------|---------|-------------|
| `ENGINE_URL` | `http://localhost:7001` | URL of ws-engine |
| `ENGINE_API_KEY` | _(unset)_ | API key sent to ws-engine |
| `WS_API_KEY` | _(unset)_ | API key required from clients |

### ws-client / ws-cli (legacy)

| Variable | Default | Description |
|----------|---------|-------------|
| `WS_API_URL` | `http://localhost:7000` | ws-api base URL |
| `WS_API_KEY` | _(empty)_ | API key for ws-api |
