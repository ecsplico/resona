# whisper-server task runner
# https://github.com/casey/just

# Show available recipes
default:
    @just --list

# ── Setup ─────────────────────────────────────────────────────────────

# Install all workspace packages (required after clone or adding deps)
install:
    uv sync --all-packages --no-build-isolation-package openai-whisper

# Add a dependency to a specific package  (e.g: just add resona-api httpx)
add package dep:
    uv add --package {{ package }} {{ dep }}

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

# ── Backends ──────────────────────────────────────────────────────────

# List configured backends and their reachability
backends:
    uv run resona backends list

# ── Docs ──────────────────────────────────────────────────────────────

# Start the MkDocs dev server with live reload on :8000
docs:
    uv run mkdocs serve

# Build static docs to site/
docs-build:
    uv run mkdocs build
