FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir uv
# README.md is required: pyproject.toml declares `readme = "README.md"`, so uv sync
# aborts without it.
COPY pyproject.toml uv.lock* README.md ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev || uv sync --no-dev
CMD ["uv", "run", "resona-project-worker"]
