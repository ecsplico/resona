# Environment Variables

All configuration is read with [`python-decouple`](https://github.com/HBNetwork/python-decouple): environment variables take precedence over values in a `.env` file. Copy `.env.example` to `.env` to get started.

!!! note "Exception: resona-client"
    `resona-client` uses `os.getenv()` for `RESONA_API_URL` and `RESONA_API_KEY` because it has no `python-decouple` dependency. All other packages use `config()` from `python-decouple`.

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
| `RESONA_API_KEY` | _(unset)_ | API key required from clients; auth is disabled when not set |
| `DATA_PATH` | `./data` | Root directory for all persistent data (audio files, SQLite DB, transcripts) |
| `LOGLEVEL` | `info` | Log level: `debug`, `info`, `warning`, `error` |

## resona-client / resona CLI

| Variable | Default | Description |
|----------|---------|-------------|
| `RESONA_API_URL` | `http://localhost:7000` | resona-api base URL |
| `RESONA_API_KEY` | _(unset)_ | API key sent to resona-api (`X-API-Key` header) |

## resona-postprocess

| Variable | Default | Description |
|----------|---------|-------------|
| `RESONA_LLM_MODEL` | `gpt-4o-mini` | Default LLM model for postprocessing pipeline steps |
| `RESONA_LLM_API_BASE` | _(unset)_ | Custom LLM endpoint, e.g. a local Ollama instance; passed to litellm |

## Cloud provider keys

Cloud STT and TTS providers are activated **automatically** when their API key variable is present. No additional configuration is needed — set the key and the provider appears in `GET /v1/engines`.

API keys are read from environment variables at call time and are never stored in `~/.resona/config.json`.

| Variable | Provider | Activates |
|----------|----------|-----------|
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

!!! tip "API key setup"
    If you set `RESONA_API_KEY` in `.env`, all clients must send `X-API-Key: <key>` with every request. Export the same value as `RESONA_API_KEY` in your shell (or add it to `~/.bashrc`) so the `resona` CLI picks it up automatically.

!!! tip "Custom LLM endpoint"
    To use a local Ollama instance for postprocessing instead of OpenAI, set:
    ```bash
    RESONA_LLM_MODEL=ollama/llama3
    RESONA_LLM_API_BASE=http://localhost:11434
    ```
    Any model supported by [litellm](https://docs.litellm.ai/docs/providers) works here.
