# Getting Started

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- `ffmpeg` in PATH (for audio loading)
- NVIDIA GPU with CUDA 12.8+ (for resona-engine)

## Local development

```bash
# 1. Clone the repository
git clone <repo-url>
cd resona

# 2. Install all workspace packages
#    openai-whisper needs --no-build-isolation-package due to a pkg_resources issue
uv sync --all-packages --no-build-isolation-package openai-whisper

# 3. Copy and edit environment config
cp .env.example .env
# Edit .env — set RESONA_BACKEND, model names, optional API keys

# 4. Start the engine (GPU required, keep this terminal open)
uv run resona-engine-faster-whisper

# 5. Start the API (CPU-only, separate terminal)
uv run resona-api

# 6. Verify both services are healthy
curl http://localhost:7001/health
curl http://localhost:7000/health
```

## Docker (recommended for deployment)

```bash
cp .env.example .env
# Edit .env — set RESONA_API_KEY and model names at minimum

docker compose -f docker-compose.resona.yml up -d

# The engine container starts first; the API waits for it to be healthy
curl http://localhost:7001/health
curl http://localhost:7000/health
```

The engine uses `nvidia/cuda:12.8.0-runtime-ubuntu24.04` and requires the NVIDIA container runtime. The API uses `python:3.12-slim`.

## First transcription

```bash
# Submit an audio file
curl -X POST http://localhost:7000/jobs \
  -F "audio_files=@recording.wav"

# Returns: [{"id": 1, "status": "pending", ...}]

# Poll for completion
curl http://localhost:7000/job/1

# Returns: {"id": 1, "status": "completed", "transcript": "...", "md": "..."}
```

Or use the Python client:

```python
from resona_client.client import ResonaClient

client = ResonaClient()  # reads RESONA_API_URL from env, default http://localhost:7000

job = client.submit_job("recording.wav")
result = client.wait_for_job(job["id"])
print(result["md"])  # transcript with replacements applied
```

Or use the CLI:

```bash
# Transcribe a file, glob, or directory and save results
resona transcribe ./recordings/ --output-dir ./transcripts/
resona transcribe recording.mp3
resona transcribe "recordings/*.mp3"

# Watch a directory and auto-submit any new audio files
resona watch ./inbox/
```

## TUI tools

```bash
resona rec    # Audio recorder — saves WAV files
resona live   # Live transcription — streams to engine via WebSocket
resona ui     # Record and transcribe — records, submits job, shows result
```

## Running tests

```bash
uv run pytest                                  # all packages
uv run pytest packages/engine-core/tests/      # engine core
uv run pytest packages/api/tests/              # api
uv run pytest packages/client/tests/           # client
uv run pytest apps/resona-cli/tests/           # cli
uv run pytest -k test_transcribe               # by name
```
