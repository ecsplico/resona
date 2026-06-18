FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock* ./
COPY packages/directus-transcribe/ ./packages/directus-transcribe/
RUN uv sync --package resona-directus-transcribe --frozen --no-dev
CMD ["uv", "run", "resona-directus-transcribe"]
