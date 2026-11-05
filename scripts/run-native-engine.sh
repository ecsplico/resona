#!/usr/bin/env bash
# Run a Resona ASR engine natively on the Apple GPU (MLX / Metal) for the
# Mac single-host stack (docker-compose.mac.yml). Docker on macOS is CPU-only.
#
# Defaults to mlx-whisper + mlx-community/whisper-large-v3-mlx (full large-v3),
# the engine `recommended_engine()` picks on Apple Silicon. Listens on :7720 so
# the dockerized api reaches it via host.docker.internal.
#
# First run downloads the model into ~/.cache/huggingface.
set -euo pipefail

ENGINE="${RESONA_ENGINE:-mlx-whisper}"
PORT="${PORT:-7720}"

cd "$(dirname "$0")/.."

# Ensure the Apple GPU engines are installed in the workspace venv (idempotent).
uv sync --all-packages --extra apple >/dev/null

echo "→ ${ENGINE} on :${PORT}  (model: ${DEFAULT_MLX_WHISPER_MODEL:-mlx-community/whisper-large-v3-mlx})"
RESONA_ENGINE="$ENGINE" PORT="$PORT" exec uv run "resona-engine-${ENGINE}"
