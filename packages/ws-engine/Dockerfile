FROM nvidia/cuda:12.8.0-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    python3.12 \
    python3.12-dev \
    python3-pip \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.cargo/bin:/root/.local/bin:$PATH"

WORKDIR /app

# Copy workspace files for uv workspace resolution
COPY pyproject.toml uv.lock* ./
COPY packages/ws-engine/ ./packages/ws-engine/
# Copy other workspace packages that ws-engine might reference at build time
RUN uv sync --package ws-engine --frozen --no-dev

EXPOSE 7001

CMD ["uv", "run", "--package", "ws-engine", "ws-engine"]
