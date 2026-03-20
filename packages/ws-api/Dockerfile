FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.cargo/bin:/root/.local/bin:$PATH"

WORKDIR /app

# Copy workspace files for uv workspace resolution
COPY pyproject.toml uv.lock* ./
COPY packages/ws-api/ ./packages/ws-api/

RUN uv sync --package ws-api --frozen --no-dev

EXPOSE 7000

CMD ["uv", "run", "--package", "ws-api", "ws-api"]
