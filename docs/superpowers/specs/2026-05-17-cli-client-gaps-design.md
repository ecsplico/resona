# CLI & Client Gaps — Design Spec

**Date:** 2026-05-17
**Status:** Approved

## Overview

The unified STT/TTS API (built in the previous session) added three new gateway
routes (`GET /v1/engines`, `POST /v1/audio/transcriptions`,
`POST /v1/audio/speech`) but left the client library and CLI without coverage for
them. This spec closes those gaps and makes the new routes first-class citizens
of the developer experience.

## Scope

1. **Client library** — three new methods on `ResonaClient`
2. **`resona transcribe` redesign** — sync gateway by default, local fallback
3. **`resona submit`** — new async-queue command that returns a job URL
4. **`resona speech`** — new TTS command
5. **`resona engines status`** — new discovery subcommand
6. **Justfile** — per-profile Docker shortcuts, `format`, `lint` targets
7. **Client tests** — respx-mocked tests for all three new client methods

---

## 1. Client Library

**File:** `packages/client/src/resona_client/client.py`

Three new methods are added to `ResonaClient`. All follow the existing patterns:
`self._base_url`, `self._headers`, `self._client` (httpx), and raise
`httpx.HTTPStatusError` on non-2xx responses.

### `list_engines() → dict`

```python
def list_engines(self) -> dict:
    r = self._client.get(f"{self._base_url}/v1/engines", headers=self._headers)
    r.raise_for_status()
    return r.json()
```

Returns `{"engines": [...], "default": "name-or-null"}` as defined by the
gateway's `EngineInfo` serialisation.

### `create_transcription(audio_path, ...) → dict`

```python
def create_transcription(
    self,
    audio_path: Path,
    *,
    model: str = "whisper-1",
    language: str | None = None,
    prompt: str | None = None,
    response_format: str = "json",
    engine: str | None = None,
    private: bool = False,
) -> dict:
```

POSTs multipart to `POST /v1/audio/transcriptions`. Returns the parsed JSON
response (`{"text": ..., "language": ..., "segments": [...]}`).
`response_format` is always `"json"` for the client method — the raw-text and
verbose variants are available directly via the API.

### `create_speech(text, ...) → bytes`

```python
def create_speech(
    self,
    text: str,
    *,
    model: str = "tts-1",
    voice: str = "alloy",
    response_format: str = "mp3",
    speed: float = 1.0,
    engine: str | None = None,
    private: bool = False,
) -> bytes:
```

POSTs JSON to `POST /v1/audio/speech`. Returns raw audio bytes. The caller is
responsible for writing to disk or streaming.

---

## 2. `resona transcribe` Redesign

**File:** `apps/resona-cli/src/resona_cli/transcribe.py`

### Default path — sync gateway

When `RESONA_API_URL` is configured and reachable, `resona transcribe` calls
`client.create_transcription()` (`POST /v1/audio/transcriptions`) and prints the
transcript to stdout (or writes to `--output-dir` if given). The gateway owns
all engine routing — the CLI just POSTs and receives text.

### Fallback — local engine

If the gateway is unreachable (connection error), the command falls back to the
existing local-engine path (InProcessEngine / LocalEngine). The fallback is
silent; the user sees the transcript regardless of which path ran.

### Interface

```
resona transcribe FILE... [--engine NAME] [--private] [--language LANG]
                          [--output-dir DIR] [--model NAME]
```

`--engine` is forwarded as the `engine` form field to the gateway (or used for
local-engine selection on fallback). `--private` is forwarded as the `private`
boolean field.

### Simplification

Most of the existing multi-path engine-resolution code in `transcribe.py`
(cloud-direct branch, config.json resolution, local-engine spawning for server
path) can be removed. The gateway handles routing. Only the local-engine
fallback branch is retained.

---

## 3. `resona submit`

**File:** `apps/resona-cli/src/resona_cli/submit.py` (new file)

New top-level command. Submits one or more files to the async job queue
(`POST /jobs`) and immediately prints the result URL — no polling.

```
resona submit FILE... [--engine NAME] [--language LANG] [--translate]
```

Output (one line per file):
```
http://localhost:7000/job/abc-123
http://localhost:7000/job/def-456
```

The URL is `{RESONA_API_URL}/job/{id}` — the caller can poll it or pass it
downstream. `--translate` sets `translate=True` on the job.

Registered in `main.py` alongside `transcribe`.

---

## 4. `resona speech`

**File:** `apps/resona-cli/src/resona_cli/speech.py` (new file)

New top-level command. Calls `client.create_speech()` and writes audio to disk
or plays it.

```
resona speech TEXT [--output PATH] [--engine NAME] [--voice NAME]
                   [--model NAME]  [--format mp3|opus|aac|flac]
                   [--speed FLOAT] [--play]
```

- `TEXT` — positional argument; the string to synthesise
- `--output PATH` — write audio here; defaults to `speech.mp3` in cwd; `-` for stdout
- `--play` — pipe audio to the first available player: `aplay`, `afplay`, `mpv`
  (tried in that order); warns and exits cleanly if none found; mutually
  exclusive with `--output -`
- `--engine`, `--voice`, `--model`, `--format`, `--speed` — forwarded directly
  to `create_speech()`

Error handling: non-2xx from the gateway → print the error body and exit 1.

Registered in `main.py`.

---

## 5. `resona engines status`

**File:** `apps/resona-cli/src/resona_cli/engines.py` (existing, add subcommand)

New subcommand under the `engines` group. Calls `list_engines()` and renders a
table using `rich` (already a transitive dep via Textual) or plain text if rich
is unavailable.

```
resona engines status
```

Output columns: `Name`, `Kind` (local/cloud), `Capabilities` (stt/tts),
`Available` (✓/✗), `Models`.

The existing `resona engines list` (which lists `config.json` entries) is
unchanged — `status` shows the live gateway catalogue.

---

## 6. Justfile

**File:** `justfile`

### Per-profile Docker shortcuts

```just
up-faster-whisper:
    docker compose -f docker-compose.resona.yml --profile faster-whisper up -d

up-whisper:
    docker compose -f docker-compose.resona.yml --profile whisper up -d

up-voxtral:
    docker compose -f docker-compose.resona.yml --profile voxtral up -d
```

### Format and lint

```just
format:
    uv run ruff format .
    uv run ruff check --fix .

lint:
    uv run ruff check .
```

---

## 7. Client Tests

**File:** `packages/client/tests/test_client.py` (extend existing)

Three new tests using `respx.mock`:

- `test_list_engines` — mocks `GET /v1/engines`, asserts return value has
  `"engines"` key
- `test_create_transcription` — mocks `POST /v1/audio/transcriptions`, sends a
  small WAV fixture, asserts `"text"` key in response
- `test_create_speech` — mocks `POST /v1/audio/speech`, asserts return value is
  `bytes`

---

## Testing Strategy

- **Client:** respx mocks for all three new methods (unit tests, no real server)
- **CLI — `resona speech`:** Typer `CliRunner`; patch `ResonaClient.create_speech`
- **CLI — `resona submit`:** Typer `CliRunner`; patch `ResonaClient.submit_job`
- **CLI — `resona transcribe` (sync path):** patch `ResonaClient.create_transcription`
- **CLI — `resona transcribe` (fallback):** simulate connection error, assert local engine called
- **CLI — `resona engines status`:** patch `ResonaClient.list_engines`

---

## What Is NOT Changed

- `resona engines list/add/test` — manage `config.json` entries, unchanged
- `resona watch` — uses job queue, unchanged
- `resona rec`, `resona live`, `resona ui` — unchanged
- `resona-api` endpoints — no new routes needed; the three new client methods
  call existing routes
- Engine resolution inside the gateway (`engine_registry.py`) — unchanged
