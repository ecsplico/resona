# Getting Started

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- `ffmpeg` in PATH (for audio loading)
- NVIDIA GPU with CUDA 12.8+ (for ws-engine)

## Local development

```bash
# 1. Clone the repository
git clone <repo-url>
cd whisper-server

# 2. Install all workspace packages
#    openai-whisper needs --no-build-isolation-package due to a pkg_resources issue
uv sync --all-packages --no-build-isolation-package openai-whisper

# 3. Copy and edit environment config
cp .env.example .env
# Edit .env — set ASR_MODE, model names, optional API keys

# 4. Start the engine (GPU required, keep this terminal open)
uv run ws-engine

# 5. Start the API (CPU-only, separate terminal)
uv run ws-api

# 6. Verify both services are healthy
curl http://localhost:7001/health
curl http://localhost:7000/health
```

## Docker (recommended for deployment)

```bash
cp .env.example .env
# Edit .env — set WS_API_KEY and model names at minimum

docker compose up -d

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
from ws_client.client import WhisperClient

client = WhisperClient()  # reads WS_API_URL from env, default http://localhost:7000

job = client.submit_job("recording.wav")
result = client.wait_for_job(job["id"])
print(result["md"])  # transcript with replacements applied
```

Or use the CLI:

```bash
# Transcribe all files in a directory and save results
ws-cli batch ./recordings/ --output-dir ./transcripts/

# Watch a directory and auto-submit any new audio files
ws-cli watch ./inbox/
```

## TUI tools

```bash
ws-cli rec    # Audio recorder — saves WAV files
ws-cli live   # Live transcription — streams to ws-engine via WebSocket
ws-cli ui     # Record and transcribe — records, submits job, shows result
```

## Running tests

```bash
uv run pytest                         # all packages
uv run pytest packages/ws-api/tests/  # one package
uv run pytest apps/cli/tests/         # cli tests
uv run pytest -k test_transcribe      # by name
```
