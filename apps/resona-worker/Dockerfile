# Linux worker image. One Dockerfile, two variants selected by build args:
#
#   CPU  (default):  docker build -f apps/resona-worker/Dockerfile .
#   CUDA:            docker build -f apps/resona-worker/Dockerfile \
#                      --build-arg BASE_IMAGE=nvidia/cuda:13.0.1-runtime-ubuntu24.04 \
#                      --build-arg WORKER_EXTRAS="--extra cuda --extra documents" .
#
# The worker is outbound-only: no port, no Docker socket. Mount the 0600 token file
# read-only, a model cache (HF_HOME) and a disposable work directory; run
# `resona-worker prepare` once against the cache, then `resona-worker run`.
# Images are released by digest (release manifest), never consumed as :latest/:cuda.
ARG BASE_IMAGE=python:3.12-slim

# ---- build ----
FROM python:3.12-slim AS builder

ARG WORKER_EXTRAS="--extra audio-cpu --extra documents"
ENV PYTHONDONTWRITEBYTECODE=1
ENV UV_PYTHON_INSTALL_DIR=/opt/python

COPY --from=ghcr.io/astral-sh/uv:0.10.3 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock* ./
COPY apps/resona-worker/ ./apps/resona-worker/
COPY packages/job-core/ ./packages/job-core/
COPY packages/postprocess/ ./packages/postprocess/
COPY packages/asr-core/ ./packages/asr-core/
COPY packages/engine-server/ ./packages/engine-server/
COPY packages/engine-faster-whisper/ ./packages/engine-faster-whisper/
COPY packages/engine-whisper/ ./packages/engine-whisper/
COPY packages/engine-docling/ ./packages/engine-docling/

# A uv-managed CPython (not the base image's) so the same /opt/python + venv copy
# runs on the CUDA runtime base, which ships no Python 3.12 of its own.
RUN uv python install 3.12 \
    && uv venv --python 3.12 --relocatable /app/.venv \
    && uv pip install --python /app/.venv/bin/python "setuptools<81" wheel \
    && uv sync --package resona-worker ${WORKER_EXTRAS} --python /app/.venv/bin/python \
         --frozen --no-dev --no-editable --no-build-isolation-package openai-whisper --compile-bytecode

# ---- runtime ----
FROM ${BASE_IMAGE}

ARG RESONA_RELEASE_ID=""

LABEL org.opencontainers.image.title="resona-worker"
LABEL org.opencontainers.image.description="Resona outbound-only pull worker (Linux CPU or NVIDIA CUDA)"
LABEL org.opencontainers.image.source="https://github.com/ecsplico/resona"
LABEL org.opencontainers.image.documentation="https://ecsplico.github.io/resona/"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.vendor="Resona contributors"
LABEL org.opencontainers.image.version="${RESONA_RELEASE_ID}"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HF_HOME=/models
ENV HF_HUB_DISABLE_TELEMETRY=1
ENV DO_NOT_TRACK=1

# ffmpeg decodes audio; libgl1/libglib2.0-0 back OpenCV in Docling/RapidOCR.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libgl1 libglib2.0-0 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && (getent passwd 1000 >/dev/null && userdel --remove "$(getent passwd 1000 | cut -d: -f1)" || true) \
    && useradd --uid 1000 --create-home --shell /usr/sbin/nologin resona \
    && mkdir -p /models /work /state && chown resona:resona /models /work /state

COPY --from=builder /opt/python /opt/python
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

USER resona
WORKDIR /state
ENTRYPOINT ["resona-worker"]
CMD ["--help"]
