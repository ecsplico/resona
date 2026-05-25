# ---- build ----
FROM nvidia/cuda:13.0.1-devel-ubuntu24.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 python3.12-dev python3.12-venv \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.10.3 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock* ./
COPY packages/asr-core/ ./packages/asr-core/
COPY packages/engine-server/ ./packages/engine-server/
COPY packages/engine-voxtral/ ./packages/engine-voxtral/

RUN uv venv --python 3.12 \
    && uv sync --package resona-engine-voxtral --python 3.12 --frozen --no-dev --no-editable --compile-bytecode

# Strip build artifacts
RUN find /app/.venv -name "*.a" -delete \
    && find /app/.venv -name "*.pyc" -path "*/__pycache__/*" -delete \
    && find /app/.venv -name "__pycache__" -type d -empty -delete \
    && find /app/.venv -name "tests" -type d -exec rm -rf {} + 2>/dev/null || true \
    && find /app/.venv -name "*.dist-info" -type d -exec sh -c 'rm -f "$1"/RECORD "$1"/INSTALLER "$1"/REQUESTED' _ {} \;

# ---- runtime ----
FROM nvidia/cuda:13.0.1-runtime-ubuntu24.04

LABEL org.opencontainers.image.title="resona-engine-voxtral"
LABEL org.opencontainers.image.description="HuggingFace Transformers ASR engine for Resona (Voxtral, Whisper, etc.); HTTP/WS on :7001"
LABEL org.opencontainers.image.source="https://github.com/ecsplico/resona"
LABEL org.opencontainers.image.documentation="https://ecsplico.github.io/resona/"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.vendor="Resona contributors"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 7001
CMD ["python3.12", "-m", "resona_engine_server.run"]
