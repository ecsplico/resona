# whisper-server task runner
# https://github.com/casey/just

# Show available recipes
default:
    @just --list

# ── Setup ─────────────────────────────────────────────────────────────

# Install all workspace packages into the local venv (after clone or dep change)
install:
    uv sync --all-packages --no-build-isolation-package openai-whisper

# Add a dependency to a specific package  (e.g: just add resona-api httpx)
add package dep:
    uv add --package {{ package }} {{ dep }}

# ── Install as a system-wide tool (uv tool install) ───────────────────
# Installs `resona` onto $PATH without needing `uv run`. The same recipes
# work on Linux and macOS: the CLI's pyproject pins the cu130 torch index
# behind a `sys_platform == 'linux'` marker, so torch resolves from the CUDA
# wheels on Linux and from default PyPI (CPU/MPS) on macOS. That index config
# travels with `--from ./apps/resona-cli`, so no --index flag is needed here.
# If a build ever fails, fall back to `just install` + `uv run resona`.
#
# NOTE: never run `uv tool install .` at the workspace root — that's a
# meta-package with no build backend. Always install from apps/resona-cli.
# Extras must live INSIDE --from (`'./apps/resona-cli[extra]'`); passing the
# extra as a separate positional conflicts with the path source in newer uv.

# Default install (Linux + macOS): record/live TUIs + faster-whisper (torch-free)
install-cli:
    uv tool install --force --from ./apps/resona-cli resona-cli

# Available extras:
#   whisper, voxtral ........ PyTorch engines (Linux + macOS)
#   parakeet ................ NVIDIA NeMo engine (Linux CUDA/CPU)
#   mlx, whisper-cpp, lightning-mlx, apple .. Apple Silicon GPU engines (macOS)
#   all ..................... every engine

# Install with an engine extra, e.g. `just install-cli-with whisper`
install-cli-with extra:
    uv tool install --force --from './apps/resona-cli[{{ extra }}]' resona-cli

# Default + OpenAI Whisper (PyTorch) engine
install-cli-whisper: (install-cli-with "whisper")
# Default + Voxtral / HuggingFace Transformers engine
install-cli-voxtral: (install-cli-with "voxtral")
# Default + NVIDIA Parakeet (NeMo) engine — Linux CUDA/CPU
install-cli-parakeet: (install-cli-with "parakeet")
# Default + Apple Silicon GPU engines (mlx-whisper, whisper.cpp, lightning-mlx) — macOS arm64
install-cli-apple: (install-cli-with "apple")
# Default + every engine
install-cli-all: (install-cli-with "all")

# Uninstall the resona-cli tool
uninstall-cli:
    uv tool uninstall resona-cli

# ── Services (local) ──────────────────────────────────────────────────

# Start the transcription engine on :7001 (GPU required)
engine:
    uv run resona-engine-faster-whisper

# Start the job queue API on :7000
api:
    uv run resona-api

# ── Docker ────────────────────────────────────────────────────────────

# Start all services in the background
up:
    docker compose -f docker-compose.resona.yml up -d

# Stop all services
down:
    docker compose -f docker-compose.resona.yml down

# Follow logs for all services (or a specific one: just logs engine)
logs service="":
    docker compose -f docker-compose.resona.yml logs -f {{ service }}

# Rebuild images and restart
rebuild:
    docker compose -f docker-compose.resona.yml up -d --build

# ── Build images (no start) ───────────────────────────────────────────
# Engine builds need their profile to be selected — services behind a
# profile are invisible to compose otherwise. The api has no profile, so
# `build-api` works without one.

# Build every image (all engines + api)
build:
    docker compose -f docker-compose.resona.yml \
        --profile faster-whisper --profile whisper --profile voxtral \
        build

# Build only the faster-whisper engine image
build-faster-whisper:
    docker compose -f docker-compose.resona.yml --profile faster-whisper build engine-faster-whisper

# Build only the whisper (PyTorch) engine image
build-whisper:
    docker compose -f docker-compose.resona.yml --profile whisper build engine-whisper

# Build only the voxtral engine image
build-voxtral:
    docker compose -f docker-compose.resona.yml --profile voxtral build engine-voxtral

# Build only the API image
build-api:
    docker compose -f docker-compose.resona.yml build api

# ── Start backend containers ──────────────────────────────────────────

# Start only the faster-whisper engine + API
up-faster-whisper:
    docker compose -f docker-compose.resona.yml --profile faster-whisper up -d

# Start only the whisper (PyTorch) engine + API
up-whisper:
    docker compose -f docker-compose.resona.yml --profile whisper up -d

# Start only the voxtral engine + API
up-voxtral:
    docker compose -f docker-compose.resona.yml --profile voxtral up -d

# Start the faster-whisper engine + API on a CPU-only host (no NVIDIA GPU)
up-cpu:
    docker compose -f docker-compose.resona.yml -f docker-compose.cpu.yml \
        --profile faster-whisper up -d

# ── GHCR pre-built images ─────────────────────────────────────────────
# Once a tagged release runs the release workflow, images are available at
# ghcr.io/ecsplico/resona-*. `pull` fetches the latest without rebuilding.

# Pull the latest pre-built images from GHCR
pull:
    docker compose -f docker-compose.resona.yml \
        --profile faster-whisper --profile whisper --profile voxtral \
        pull

# ── Tests ─────────────────────────────────────────────────────────────

# Run all tests (pass extra args: just test -k test_transcribe)
test *args:
    uv run pytest {{ args }}

# Run tests for a single package
test-api:
    uv run pytest packages/api/tests/

test-engine:
    uv run pytest packages/engine-server/tests/ packages/asr-core/tests/

test-client:
    uv run pytest packages/client/tests/

test-cli:
    uv run pytest apps/resona-cli/tests/

# ── TUI tools ─────────────────────────────────────────────────────────

# Audio recorder TUI
rec:
    uv run resona rec

# Live transcription TUI (streams to engine via WebSocket)
live:
    uv run resona live

# Record-and-transcribe TUI (records, submits job, shows result)
ui:
    uv run resona ui

# ── CLI shortcuts ─────────────────────────────────────────────────────

# Transcribe files, globs, or directories
transcribe *args:
    uv run resona transcribe {{ args }}

# Watch a directory and auto-submit new audio files
watch dir:
    uv run resona watch {{ dir }}

# ── Engines ───────────────────────────────────────────────────────────

# List configured engines and their reachability
engines:
    uv run resona engines list

# ── Local TTS engines (resona-tts-local) ──────────────────────────────
# Install local/offline TTS engine libraries into the workspace venv. The
# engine code ships with `just install`; these add the native model libs.
# kokoro + qwen are lockable extras; chatterbox needs --no-deps because its
# conservative pins (numpy<2/torch==2.6) can't co-lock (it runs fine on the
# modern stack anyway — verified).

# Kokoro-82M — tiny, CPU-realtime, cross-platform (start here)
tts-kokoro:
    uv pip install -e './packages/tts-local[kokoro]'

# Qwen3-TTS — Apple Silicon, MLX-native via mlx-audio
tts-qwen:
    uv pip install -e './packages/tts-local[qwen]'

# Chatterbox Multilingual + Turbo — installed --no-deps + its pure deps
tts-chatterbox:
    uv pip install --no-deps chatterbox-tts
    uv pip install librosa s3tokenizer diffusers resemble-perth conformer \
        omegaconf pykakasi pyloudnorm spacy-pkuseg einops

# ── Docs ──────────────────────────────────────────────────────────────

# Start the MkDocs dev server with live reload on :8000
docs:
    uv run mkdocs serve

# Build static docs to site/
docs-build:
    uv run mkdocs build

# ── Code quality ──────────────────────────────────────────────────────

# Auto-format all Python files and apply safe lint fixes
format:
    uv run ruff format .
    uv run ruff check --fix .

# Check for lint issues without modifying files
lint:
    uv run ruff check .
