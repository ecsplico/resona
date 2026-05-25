# ---- build ----
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1

COPY --from=ghcr.io/astral-sh/uv:0.10.3 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock* ./
COPY packages/api/ ./packages/api/
COPY packages/cloud-stt/ ./packages/cloud-stt/
COPY packages/cloud-tts/ ./packages/cloud-tts/
COPY packages/postprocess/ ./packages/postprocess/
RUN uv sync --package resona-api --frozen --no-dev --no-editable --compile-bytecode

# ---- runtime ----
FROM python:3.12-slim

LABEL org.opencontainers.image.title="resona-api"
LABEL org.opencontainers.image.description="Resona job queue, SQLite DB, and postprocessing API on :7000 (OpenAI-compatible /v1/audio routes)"
LABEL org.opencontainers.image.source="https://github.com/ecsplico/resona"
LABEL org.opencontainers.image.documentation="https://ecsplico.github.io/resona/"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.vendor="Resona contributors"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 7000
CMD ["python", "-m", "resona_api.run"]
