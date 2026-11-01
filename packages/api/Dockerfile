# Hub image (CPU only): job API, SQLite, worker broker, evaluations. No model or GPU
# dependencies: inference runs on approved pull workers.
# ---- build ----
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1

COPY --from=ghcr.io/astral-sh/uv:0.10.3 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock* ./
COPY packages/api/ ./packages/api/
COPY packages/asr-core/ ./packages/asr-core/
COPY packages/cloud-stt/ ./packages/cloud-stt/
COPY packages/cloud-tts/ ./packages/cloud-tts/
COPY packages/tts-local/ ./packages/tts-local/
COPY packages/postprocess/ ./packages/postprocess/
COPY packages/job-core/ ./packages/job-core/
RUN uv sync --package resona-api --frozen --no-dev --no-editable --compile-bytecode

# ---- runtime ----
FROM python:3.12-slim

# The release this image belongs to (tag without the leading "v"); reported by
# GET /v1/version and approved for every (profile, platform) tuple as the bundled release.
ARG RESONA_RELEASE_ID=""

LABEL org.opencontainers.image.title="resona-api"
LABEL org.opencontainers.image.description="Resona hub: org-scoped job API, SQLite, pull-worker broker and evaluations on :7000 (OpenAI-compatible /v1/audio routes)"
LABEL org.opencontainers.image.source="https://github.com/ecsplico/resona"
LABEL org.opencontainers.image.documentation="https://ecsplico.github.io/resona/"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.vendor="Resona contributors"
LABEL org.opencontainers.image.version="${RESONA_RELEASE_ID}"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV RESONA_RELEASE_ID="${RESONA_RELEASE_ID}"
ENV DATA_PATH=/data

RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin resona \
    && mkdir -p /data && chown resona:resona /data

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

USER resona
VOLUME ["/data"]
EXPOSE 7000
CMD ["python", "-m", "resona_api.run"]
