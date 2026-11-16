FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir uv
# README.md is required: pyproject.toml declares `readme = "README.md"`, so uv sync
# aborts without it.
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev
ENV DICTATIONS_MCP_HOST=0.0.0.0 \
	DICTATIONS_MCP_PORT=8000
EXPOSE 8000
# Up = the endpoint answers; without a bearer token that answer is 401.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
	CMD ["/app/.venv/bin/python", "-c", "import os,urllib.request,urllib.error\ntry:\n    urllib.request.urlopen('http://127.0.0.1:'+os.environ['DICTATIONS_MCP_PORT']+'/mcp',timeout=4)\nexcept urllib.error.HTTPError as e:\n    raise SystemExit(0 if e.code==401 else 1)\nraise SystemExit(1)"]
# The venv's entry point, not `uv run`: nothing is resolved or downloaded at start.
CMD ["/app/.venv/bin/resona-dictations-mcp"]
