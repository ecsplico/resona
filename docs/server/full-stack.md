# Full-Stack Setup (uv)

Run the engine and API directly on the host with `uv`. This is the recommended approach for development, staging, and single-machine deployments where Docker is not available.

## When to use this approach

- Local development — fast iteration without rebuilding images
- Staging or CI environments — no Docker daemon required
- Single-machine deployments — engine and API share the same GPU and filesystem

For production multi-machine deployments, see [Docker Deployment](docker.md).

## Prerequisites

- [uv](https://github.com/astral-sh/uv) installed
- NVIDIA GPU with CUDA 12.8+ (engine only; the API is CPU-only)
- All workspace packages installed:

```bash
uv sync --all-packages
```

## Start the stack

Open two terminals in the repo root.

**Terminal 1 — engine (requires GPU)**

```bash
uv run resona-engine-faster-whisper   # listens on :7001
```

Wait until you see a log line like `Application startup complete.` before starting the API.

**Terminal 2 — API**

```bash
uv run resona-api                      # listens on :7000
```

**Verify**

```bash
curl http://localhost:7001/health
curl http://localhost:7000/health
```

A healthy engine response:

```json
{"status": "ok", "engine": "faster-whisper", "models": ["large-v3"]}
```

## Switching engines

Use `RESONA_ENGINE` to select which ASR backend the engine-server loads. Each engine has its own entry-point script.

```bash
# OpenAI Whisper (PyTorch)
RESONA_ENGINE=whisper uv run resona-engine-whisper

# Voxtral / HuggingFace Transformers
RESONA_ENGINE=voxtral uv run resona-engine-voxtral
```

Override the model with the corresponding env var:

```bash
DEFAULT_FASTWHISPER_MODEL=medium uv run resona-engine-faster-whisper
DEFAULT_WHISPER_MODEL=base uv run RESONA_ENGINE=whisper resona-engine-whisper
DEFAULT_VOXTRAL_MODEL=openai/whisper-large-v3-turbo uv run resona-engine-voxtral
```

## Multi-engine setup

Start multiple engine instances on different ports and point the API at all of them.

**Start engines on separate ports**

```bash
# Terminal 1
PORT=7001 uv run resona-engine-faster-whisper

# Terminal 2
PORT=7002 RESONA_ENGINE=whisper uv run resona-engine-whisper
```

**Start the API with all engine URLs**

```bash
# Terminal 3
RESONA_ENGINE_URLS=http://localhost:7001,http://localhost:7002 uv run resona-api
```

The API probes all listed URLs at startup, tracks which engines are reachable, and load-balances incoming jobs across them. Use `RESONA_DEFAULT_ENGINE` to pin a default for requests that do not specify an engine.

```bash
RESONA_ENGINE_URLS=http://localhost:7001,http://localhost:7002 \
  RESONA_DEFAULT_ENGINE=faster-whisper \
  uv run resona-api
```

## Authentication

The hub always requires org-bound credentials. Create an organization and a client token on the hub host; the token is printed once:

```bash
uv run resona-admin organization create acme
uv run resona-admin credential issue --organization acme --role client

# Client side
export RESONA_API_URL=http://localhost:7000
export RESONA_API_KEY=<token>
```

Queued jobs run on pull workers, not on the hub. Prepare the worker on its machine (this verifies cached weights and runs synthetic jobs), then enroll and approve it with the tuples it tested:

```bash
uv run resona-worker prepare --profile mac-private --out caps.json      # on the worker machine
uv run resona-worker capabilities caps.json > caps-approve.json

uv run resona-admin worker enroll mac-studio --organization acme   # prints the worker token
uv run resona-admin worker approve <worker-id> --private --capabilities-file caps-approve.json

uv run resona-worker run --hub http://localhost:7000 --token-file ~/.resona/worker.token \
  --capabilities-file caps.json --work-dir /tmp/resona-worker
```

The token file must have mode 0600. On a Mac, run the worker as a launchd service instead: see [Mac Worker](mac-worker.md). The optional engine key still protects a directly exposed engine-server:

```bash
RESONA_ENGINE_KEY=secret-engine-key uv run resona-engine-faster-whisper
```

!!! tip "Using a .env file"
    Instead of exporting variables in every terminal, create a `.env` file in the repo root. `python-decouple` reads it automatically. Environment variables always take priority over `.env` values.

## Cloud providers

Cloud STT and TTS providers activate automatically when their API key is present. No extra configuration is needed — set the key and the provider appears in the engine catalogue.

```bash
OPENAI_API_KEY=sk-... \
  DEEPGRAM_API_KEY=dg-... \
  uv run resona-api
```

Check the catalogue:

```bash
curl http://localhost:7000/v1/engines
```

## Notes

- The engine process requires a GPU for model inference. The API process does not use a GPU.
- Model weights are cached in `${HOME}/.cache/huggingface` (HuggingFace models) or the faster-whisper cache directory. First startup downloads the model and is slow; subsequent starts are fast.
- The API stores jobs and transcripts in `DATA_PATH` (default `./data`). Set this to an absolute path in production to avoid confusion with the working directory.
