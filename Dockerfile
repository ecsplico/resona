FROM python:3.12-slim AS base

RUN apt-get -qq update \
    && apt-get -qq install --no-install-recommends ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies (frozen from lockfile)
RUN uv sync --frozen --no-dev --no-editable

# Copy application code
COPY src/ src/
COPY .env.example .env

# Create data directories
RUN mkdir -p data/files data/inbox data/md data/db

EXPOSE 7000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7000/health')" || exit 1

CMD ["uv", "run", "ws-server"]
