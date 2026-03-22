# Environment Variables

All configuration is read with `python-decouple`: environment variables take precedence over values in `.env`. Copy `.env.example` to `.env` to get started.

## ws-engine (`:7001`)

| Variable | Default | Description |
|----------|---------|-------------|
| `ASR_MODE` | `faster-whisper` | Backend: `faster-whisper`, `whisper`, `transformer`, `whisper-tf` |
| `DEFAULT_FASTWHISPER_MODEL` | `large-v3` | Model name/path for faster-whisper |
| `DEFAULT_WHISPER_MODEL` | `large-v3` | Model name/path for openai-whisper |
| `DEFAULT_TRANSFORMER_MODEL` | — | HuggingFace model ID for transformer backends |
| `ENGINE_API_KEY` | _(unset)_ | Optional API key; auth disabled if not set |
| `CORS_ORIGINS` | `*` | Comma-separated allowed CORS origins |
| `LOGLEVEL` | `info` | Log level (`debug`, `info`, `warning`, `error`) |

## ws-api (`:7000`)

| Variable | Default | Description |
|----------|---------|-------------|
| `ENGINE_URL` | `http://localhost:7001` | URL of the ws-engine service |
| `ENGINE_API_KEY` | _(unset)_ | API key sent to ws-engine (`X-API-Key`) |
| `WS_API_KEY` | _(unset)_ | API key required from clients; auth disabled if not set |
| `DATA_PATH` | `./data` | Root directory for all data |
| `FILE_PATH` | `$DATA_PATH/files` | Audio file storage directory |
| `DB_PATH` | `$DATA_PATH/db` | SQLite database directory |
| `LOGLEVEL` | `info` | Log level |

## ws-client / ws-cli

| Variable | Default | Description |
|----------|---------|-------------|
| `WS_API_URL` | `http://localhost:7000` | ws-api base URL (overrides `config.json`) |
| `WS_API_KEY` | _(empty)_ | API key for ws-api (`X-API-Key` header) |
| `FILE_PATH` | `./data/files` | Output directory for recorded audio |
| `SAMPLE_RATE` | `44100` | Microphone sample rate (Hz) |
| `CHANNELS` | `1` | Microphone channel count |
| `MD_PATH` | `./data/md` | Directory for saved Markdown transcripts |

## Docker Compose

When using `docker compose`, variables are loaded from the `.env` file in the project root (via `env_file: [.env]` in the `api` service). The engine variables can also be set inline in `docker-compose.yml`.

```bash
cp .env.example .env
# Edit .env
docker compose up -d
```

!!! tip "API key setup"
    If you set `WS_API_KEY` in `.env`, all clients must send `X-API-Key: <key>` with every request. Set the same value in `WS_API_KEY` for `ws-cli` (or export it in your shell).
