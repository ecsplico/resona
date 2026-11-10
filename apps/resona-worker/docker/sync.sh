#!/bin/sh
# Installs resona-worker from uv.lock into the image venv (builder stage of
# apps/resona-worker/Dockerfile).
#
#   sync.sh <venv python> <worker extras, e.g. --extra audio-cpu --extra documents>
#
# uv.lock pins torch to the cu130 wheels on Linux: workspace dev
# (`uv sync --all-packages`) and the CUDA image need them. A CPU image must not carry
# that stack (~10 GB of nvidia-* wheels), so without the `cuda` extra this syncs
# everything from the lock except torch, torchvision and the CUDA runtime (nvidia-*,
# cuda-*, triton), then installs the locked torch/torchvision versions from the PyTorch
# CPU index (TORCH_CPU_INDEX). faster-whisper's nvidia-cublas/cudnn-cu12 are skipped
# too: CTranslate2 only loads them for CUDA (see resona_engine_faster_whisper._cuda_libs).
set -eu

python=$1
shift

uv_sync() {
    uv sync --package resona-worker "$@" --python "$python" --frozen --no-dev --no-editable \
        --no-build-isolation-package openai-whisper --compile-bytecode
}

# `--extra cuda`, `--extra=cuda` or `--all-extras`: the CUDA image takes the lock as is.
case " $* " in
    *" cuda "* | *" --extra=cuda "* | *" --all-extras "*)
        uv_sync "$@"
        exit 0
        ;;
esac

# Linux pins of the requested extras (the export is universal; drop the non-Linux torch line).
locked=$(uv export --frozen --package resona-worker "$@" --no-dev --no-emit-workspace \
    --no-hashes --no-header --no-annotate | grep -v "sys_platform != 'linux'")
skip=$(printf '%s\n' "$locked" \
    | sed -n -E 's/^(torch|torchvision|triton|nvidia-[A-Za-z0-9._-]+|cuda-[A-Za-z0-9._-]+)==.*/\1/p')
# torch==2.12.0+cu130 -> torch==2.12.0 (matches 2.12.0+cpu and the untagged aarch64 wheels).
pins=$(printf '%s\n' "$locked" | sed -n -E 's/^(torch|torchvision)==([^+ ;]+).*/\1==\2/p')

no_install=""
for name in $skip; do
    no_install="$no_install --no-install-package $name"
done
# shellcheck disable=SC2086  # one word per flag/package name
uv_sync "$@" $no_install

if [ -n "$pins" ]; then
    # Hashes come from the PyTorch index (uv checks its sha256 fragments); uv.lock holds
    # only the cu130 wheels' hashes, so the CPU wheels are pinned by version.
    # shellcheck disable=SC2086
    uv pip install --python "$python" --index-url "${TORCH_CPU_INDEX:-https://download.pytorch.org/whl/cpu}" \
        --no-deps --compile-bytecode $pins
fi
