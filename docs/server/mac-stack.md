# Mac Single-Host Stack (Apple Silicon)

Run resona-api and an ASR engine on one Apple Silicon Mac, for local development or a single-user setup. For serving jobs from a Mac to a remote hub, use the [Mac Worker](mac-worker.md) instead.

## Why the engine is not in Docker

MLX/Metal cannot run inside a Linux container, and Docker on macOS has no GPU passthrough, so any engine in Docker is CPU-only. The engine therefore runs natively on the Apple GPU and only the api runs in Docker.

| Component | Where | Port | Default |
|---|---|---|---|
| `mlx-whisper` engine (GPU, large-v3) | native (`scripts/run-native-engine.sh`) | 7720 | on |
| `api` (resona-api) | Docker (`docker-compose.mac.yml`) | 7710 | on |
| `engine-faster-whisper` (CPU fallback) | Docker, profile `cpu-engine` | 7720 | off |

The native engine uses `mlx-community/whisper-large-v3-mlx`, the engine and model `recommended_engine()` picks on Apple Silicon.

## Run

From the repository root:

```bash
scripts/run-native-engine.sh                      # terminal 1: native MLX engine on :7720
docker compose -f docker-compose.mac.yml up -d    # terminal 2: api on :7710
```

Or `just mac-engine` and `just mac-up`.

- api: <http://localhost:7710> (`/health`, OpenAI-compatible `/v1/audio/*`)
- engine: <http://localhost:7720/health>

The first engine start runs `uv sync --all-packages --extra apple` and downloads the model into `~/.cache/huggingface`. Override the engine or model with `RESONA_ENGINE`, `PORT` and `DEFAULT_MLX_WHISPER_MODEL`.

## Add the PWA data layer

Directus and the project worker live in `apps/resona-pwa`. Their compose file targets an api on host port 7710 by default, so it works against this stack unchanged:

```bash
cd apps/resona-pwa
cp .env.stack.example .env    # set DIRECTUS_TOKEN and RESONA_API_KEY (an org token from resona-admin)
docker compose -f docker-compose.backend.yml up -d
```

## CPU-only fallback

Runs faster-whisper in Docker instead of the native engine. It is slow at large-v3 on CPU; set `FASTWHISPER_MODEL=small` for usable latency.

```bash
docker compose -f docker-compose.mac.yml --profile cpu-engine up -d --build
```

## Stop

```bash
docker compose -f docker-compose.mac.yml --profile cpu-engine down   # add --volumes to wipe data
```

Stop the native engine with Ctrl-C in its terminal.
