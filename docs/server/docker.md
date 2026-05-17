# Docker Deployment

Deploy Resona as a set of Docker containers. This is the recommended approach for production and any shared server environment.

## Prerequisites

- Docker Engine 24+ with the Compose plugin
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) installed and configured
- An NVIDIA GPU with CUDA 12.8+ support

!!! warning "GPU requirement"
    Every engine container (`engine-faster-whisper`, `engine-whisper`, `engine-voxtral`) requires an NVIDIA GPU. The API container is CPU-only and needs no GPU. Without the NVIDIA Container Toolkit the engine containers will fail to start.

## Setup

**1. Clone the repository**

```bash
git clone https://github.com/ecsplico/resona.git
cd resona
```

**2. Create the environment file**

```bash
cp .env.example .env
```

Open `.env` and configure the variables you need. At minimum, set `DATA_PATH` to a persistent location on your host. See the [key variables table](#key-variables) below.

**3. Start the stack**

The compose file uses [profiles](https://docs.docker.com/compose/profiles/) to control which engine containers start. Activate the profile for the engine you want:

```bash
# faster-whisper (recommended default)
docker compose -f docker-compose.resona.yml --profile faster-whisper up -d

# OpenAI Whisper (PyTorch)
docker compose -f docker-compose.resona.yml --profile whisper up -d

# Voxtral / HuggingFace Transformers
docker compose -f docker-compose.resona.yml --profile voxtral up -d
```

The API container always starts regardless of which profile is active.

**4. Verify health**

The engine container takes a few minutes on first start while the model downloads. Wait for the health check to pass before the API starts accepting jobs.

```bash
# Engine health (port depends on which engine is running)
curl http://localhost:7001/health   # faster-whisper
curl http://localhost:7002/health   # whisper (if started)
curl http://localhost:7003/health   # voxtral (if started)

# API health
curl http://localhost:7000/health
```

A healthy response looks like:

```json
{"status": "ok", "engine": "faster-whisper", "models": ["large-v3"]}
```

## Key variables

The following variables are the most important to set in `.env`. For the full list see [Environment Variables](../configuration/environment.md).

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_PATH` | `./data` | Host path for persistent data (transcripts, DB, audio files). Mount this as a volume. |
| `RESONA_API_KEY` | _(unset)_ | API auth key; clients must send `X-API-Key: <key>`. Auth is disabled when not set. |
| `RESONA_ENGINE_KEY` | _(unset)_ | Engine auth key for engine-to-engine calls. Auth is disabled when not set. |
| `DEFAULT_FASTWHISPER_MODEL` | `large-v3` | faster-whisper model to load |
| `DEFAULT_WHISPER_MODEL` | `large-v3` | OpenAI Whisper model to load |
| `DEFAULT_VOXTRAL_MODEL` | `openai/whisper-large-v3` | HuggingFace model ID for voxtral |
| `DEEPGRAM_API_KEY` | _(unset)_ | Activates Deepgram STT + TTS in the engine catalogue |
| `ELEVENLABS_API_KEY` | _(unset)_ | Activates ElevenLabs STT + TTS in the engine catalogue |
| `OPENAI_API_KEY` | _(unset)_ | Activates OpenAI STT + TTS in the engine catalogue |

!!! tip "Persist your data"
    The compose file mounts `./data` inside the API container. Set `DATA_PATH=/srv/resona/data` (or any path outside the repo) in `.env` and update the `volumes` entry in `docker-compose.resona.yml` accordingly, so transcripts and the SQLite database survive container recreation.

## GPU setup

Engine containers are built on `nvidia/cuda:12.8.0-runtime-ubuntu24.04`. Compose requests a GPU via:

```yaml
deploy:
  resources:
    reservations:
      devices:
        - capabilities: [gpu]
```

To verify the NVIDIA runtime is available before starting:

```bash
docker run --rm --gpus all nvidia/cuda:12.8.0-runtime-ubuntu24.04 nvidia-smi
```

If this fails, check that `nvidia-container-toolkit` is installed and that the Docker daemon has `"default-runtime": "nvidia"` set in `/etc/docker/daemon.json`.

## Multi-engine deployment

Run multiple engine containers simultaneously and point the API at all of them. The API load-balances across the listed URLs and tracks which engine handled each job.

**1. Start multiple engine profiles**

```bash
docker compose -f docker-compose.resona.yml \
  --profile faster-whisper \
  --profile whisper \
  up -d
```

This starts `engine-faster-whisper` on port 7001 and `engine-whisper` on port 7002.

**2. Configure RESONA_ENGINE_URLS**

Add to `.env`:

```bash
RESONA_ENGINE_URLS=http://engine-faster-whisper:7001,http://engine-whisper:7001
```

The compose file already sets this variable to include all three engine service names. When a profile is inactive its container simply does not start, but the URL remains in the list — the API skips unreachable engines gracefully.

**3. Pin a default engine (optional)**

```bash
RESONA_DEFAULT_ENGINE=faster-whisper
```

When set, the API routes jobs that do not specify an engine to this one. Without it the API picks the first reachable engine.

## Logs and restart policy

All containers use `restart: unless-stopped`. View logs with:

```bash
docker compose -f docker-compose.resona.yml logs -f api
docker compose -f docker-compose.resona.yml logs -f engine-faster-whisper
```

## Updating

```bash
docker compose -f docker-compose.resona.yml --profile faster-whisper pull
docker compose -f docker-compose.resona.yml --profile faster-whisper up -d
```

Model weights are cached in `${HOME}/.cache/huggingface` on the host (mounted as a volume), so re-pulls do not re-download the model.
