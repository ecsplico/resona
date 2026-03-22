# whisper-server task runner
# https://github.com/casey/just

# Show available recipes
default:
    @just --list

# ── Setup ─────────────────────────────────────────────────────────────

# Install all workspace packages (required after clone or adding deps)
install:
    uv sync --all-packages --no-build-isolation-package openai-whisper

# Add a dependency to a specific package  (e.g: just add ws-api httpx)
add package dep:
    uv add --package {{ package }} {{ dep }}

# ── Services (local) ──────────────────────────────────────────────────

# Start the transcription engine on :7001 (GPU required)
engine:
    uv run ws-engine

# Start the job queue API on :7000
api:
    uv run ws-api

# ── Docker ────────────────────────────────────────────────────────────

# Start all services in the background
up:
    docker compose up -d

# Stop all services
down:
    docker compose down

# Follow logs for all services (or a specific one: just logs engine)
logs service="":
    docker compose logs -f {{ service }}

# Rebuild images and restart
rebuild:
    docker compose up -d --build

# ── Tests ─────────────────────────────────────────────────────────────

# Run all tests (pass extra args: just test -k test_transcribe)
test *args:
    uv run pytest {{ args }}

# Run tests for a single package
test-api:
    uv run pytest packages/ws-api/tests/

test-engine:
    uv run pytest packages/ws-engine/tests/

test-client:
    uv run pytest packages/ws-client/tests/

test-cli:
    uv run pytest apps/cli/tests/

# ── TUI tools ─────────────────────────────────────────────────────────

# Audio recorder TUI
rec:
    uv run ws-cli rec

# Live transcription TUI (streams to ws-engine via WebSocket)
live:
    uv run ws-cli live

# Record-and-transcribe TUI (records, submits job, shows result)
ui:
    uv run ws-cli ui

# ── CLI shortcuts ─────────────────────────────────────────────────────

# Watch a directory and auto-submit new audio files
watch dir:
    uv run ws-cli watch {{ dir }}

# Transcribe all audio files in a directory
batch dir:
    uv run ws-cli batch {{ dir }}

# ── Backends ──────────────────────────────────────────────────────────

# List configured backends and their reachability
backends:
    uv run ws-cli backends list

# ── Docs ──────────────────────────────────────────────────────────────

# Start the MkDocs dev server with live reload on :8000
docs:
    uv run mkdocs serve

# Build static docs to site/
docs-build:
    uv run mkdocs build
