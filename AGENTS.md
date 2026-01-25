# AGENTS.md

## Project Overview

This project is an Automatic Speech Recognition (ASR) service that provides an API for transcribing audio files using OpenAI's Whisper models (via `faster-whisper` and `openai-whisper`). It supports both synchronous and asynchronous processing, handles various audio formats, and includes a web application for interaction.

## Technology Stack

- **Language**: Python 3.12+
- **Framework**: FastAPI
- **Database**: SQLModel (SQLAlchemy + Pydantic)
- **ASR Engine**: `faster-whisper` (default), `openai-whisper`
- **Package Management**: `uv`
- **Audio Processing**: `ffmpeg-python`, `sounddevice`, `soundfile`
- **Async I/O**: `aiofiles`, `asyncio`

## Project Structure

The project follows a `src` layout:

```
.
├── src/
│   ├── ws_server/       # Main FastAPI application server
│   │   ├── api/         # API endpoints and routers
│   │   └── processing/  # Audio processing and transcription logic
│   ├── ws_cli/          # Command-line interface tools
│   ├── core/            # Shared core functionality (DB models, config, paths)
│   └── recorder/        # Audio recording functionality
├── tests/               # Test suite
├── webapp/              # Web application assets
├── pyproject.toml       # Project configuration and dependencies
└── uv.lock              # Dependency lock file
```

## Code Style & Guidelines

Follow these guidelines to maintain consistency across the codebase.

### General Python Style

- **PEP 8**: Adhere to PEP 8 standards.
- **Indentation**: Use 4 spaces for indentation.
- **Line Length**: Aim for 88 characters (Black style), but 120 is acceptable for complex logic.
- **Quotes**: Double quotes `"` are preferred for strings, unless the string contains double quotes.

### Naming Conventions

- **Variables/Functions**: `snake_case` (e.g., `process_audio`, `audio_file`).
- **Classes**: `PascalCase` (e.g., `TranscribeTask`, `Job`).
- **Constants**: `UPPER_CASE` (e.g., `FILE_PATH`, `ASR_MODE`).
- **Modules**: `snake_case` (e.g., `tasks_transcribe.py`).

### Typing

- **Type Hints**: All function signatures should have type hints. Use `typing` module or standard types (Python 3.9+ style `list`, `dict` where appropriate, or `List`, `Dict` for compatibility if needed).
- **Pydantic/SQLModel**: Use Pydantic models and SQLModel for data validation and database schemas.

### Documentation

- **Docstrings**: Provide docstrings for all public modules, classes, and functions. Use Google or NumPy style docstrings.
- **Comments**: Use inline comments to explain complex logic, but prefer self-documenting code.

### Imports

Group imports in the following order:
1.  Standard library imports (e.g., `os`, `logging`, `typing`).
2.  Third-party library imports (e.g., `fastapi`, `sqlmodel`, `decouple`).
3.  Local application imports (e.g., `from core.db.models import Job`, `from .utils import run_asr`).

### Logging

- Use the global `logging` module.
- Retrieve a logger with `logger = logging.getLogger(__name__)`.
- Do not use `print` statements for production debugging.

## Development Workflow

### Package Management

This project uses `uv` for high-performance package management.

- **Install dependencies**:
  ```bash
  uv sync
  ```
- **Add a dependency**:
  ```bash
  uv add <package_name>
  ```
- **Run a command in the environment**:
  ```bash
  uv run <command>
  ```

### Running the Application

- **Start the server**:
  ```bash
  uv run python run.py
  ```
- **Run in development mode** (if applicable):
  ```bash
  uv run uvicorn src.ws_server.api.app:app --reload
  ```
