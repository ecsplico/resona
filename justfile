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
# These install `resona` so it's on $PATH without needing `uv run`.
# NOTE: never run `uv tool install .` at the workspace root — that's a
# meta-package with no build backend. Always install from apps/resona-cli.
# The *-whisper / *-voxtral / *-full recipes pass --index for the torch
# nightly wheel since `uv tool install` does NOT inherit the workspace's
# pytorch-nightly index. If it still fails, use `just install` + `uv run resona`.

# Lean HTTP client (talks to a remote resona-api server)
install-cli:
    uv tool install --force --from ./apps/resona-cli resona-cli

# + recorder TUI (`resona rec`, `resona ui`, `resona watch`)
install-cli-record:
    uv tool install --force --from ./apps/resona-cli 'resona-cli[record]'

# + live transcription TUI (`resona live`) — uses remote engine over WS
install-cli-live:
    uv tool install --force --from ./apps/resona-cli 'resona-cli[live]'

# Fully local: bundles faster-whisper backend (⚠ uses --index for torch nightly)
install-cli-faster-whisper:
    uv tool install --force \
        --index https://download.pytorch.org/whl/nightly/cu128 \
        --from ./apps/resona-cli 'resona-cli[faster-whisper]'

# Fully local with OpenAI Whisper (PyTorch) backend
install-cli-whisper:
    uv tool install --force \
        --index https://download.pytorch.org/whl/nightly/cu128 \
        --from ./apps/resona-cli 'resona-cli[whisper]'

# Fully local with Voxtral / HuggingFace Transformers backend
install-cli-voxtral:
    uv tool install --force \
        --index https://download.pytorch.org/whl/nightly/cu128 \
        --from ./apps/resona-cli 'resona-cli[voxtral]'

# Live TUI + local faster-whisper engine (live-from-mic, no server)
install-cli-full:
    uv tool install --force \
        --index https://download.pytorch.org/whl/nightly/cu128 \
        --from ./apps/resona-cli 'resona-cli[record,live,faster-whisper]'

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
