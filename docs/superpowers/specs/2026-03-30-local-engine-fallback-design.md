# Local Engine Fallback — Design Spec

**Date:** 2026-03-30
**Status:** Approved

## Problem

`ws-cli batch` and `ws-cli watch` currently fail hard when no ws-api/ws-engine server is configured or reachable. Users with `ws-engine` installed locally should be able to transcribe files without standing up the full server stack.

## Chosen Approach

**New `LocalEngine` class in ws-cli** (`apps/cli/src/ws_cli/local_engine.py`).

`batch` and `watch` catch the `RuntimeError` from `WhisperClient.from_config()`, instantiate `LocalEngine` instead, and call it directly — bypassing the job queue entirely. ws-client stays a pure HTTP client library.

## Architecture & Components

### New file: `apps/cli/src/ws_cli/local_engine.py`

A `LocalEngine` context manager that:

1. Finds a free local TCP port.
2. Spawns `uv run ws-engine` as a subprocess with the current env inherited (so `DEFAULT_FASTWHISPER_MODEL`, `ASR_MODE`, etc. are picked up), plus `PORT=<chosen>` injected and `ENGINE_API_KEY` cleared.
3. Polls `GET /health` until HTTP 200 (configurable timeout, default 120s). Prints progress dots while waiting.
4. Exposes `transcribe(filepath: Path, language: str = "de") -> dict` — POSTs multipart audio to `/transcribe`, returns `{text, md, language}`.
5. `__exit__` sends `SIGTERM` to the subprocess and waits for it to exit. An `atexit` handler is registered as a safety net.

### Changes to `batch.py`

- Add `--model` option (str, default None).
- Add `--engine-timeout` option (float, default 120s).
- Wrap `WhisperClient.from_config()` in try/except `RuntimeError`.
- On fallback: print warning, start `LocalEngine`, transcribe files synchronously, write `<stem>.txt` next to each audio file (or into `--output-dir`). No job IDs.

### Changes to `watch.py`

- Add `--model` option (str, default None).
- Add `--engine-timeout` option (float, default 120s).
- Wrap `WhisperClient.from_config()` in try/except `RuntimeError`.
- On fallback: start `LocalEngine` once before the watch loop, then call `engine.transcribe(f)` per file, writing `<stem>.txt` next to each audio file.

## Data Flow

### Normal path (server reachable) — unchanged

```
batch/watch → WhisperClient.from_config() → submit_job() → ws-api → ws-engine
                                                          ↑
                                              wait_for_job() polls status
```

### Fallback path (no server)

```
batch/watch → WhisperClient.from_config() raises RuntimeError
            → LocalEngine(port, model, env) starts subprocess: uv run ws-engine
            → polls GET :PORT/health until 200
            → for each file:
                POST :PORT/transcribe (multipart, audio bytes)
                ← {text, md, language}
                write <stem>.txt next to audio file (or --output-dir)
```

## Output Files

Transcripts written immediately after each file completes (synchronous). Default: `<audio_stem>.txt` in the same directory as the audio file. `--output-dir` overrides, consistent with the existing `batch` behaviour.

## Error Handling

| Scenario | Behaviour |
|---|---|
| Subprocess fails to start (ws-engine not installed, bad model) | `LocalEngine.__enter__` raises `RuntimeError`; batch/watch print error and exit non-zero |
| Health poll timeout (model too slow to load) | `RuntimeError` after `--engine-timeout` seconds |
| `/transcribe` fails for a single file | Warning logged, processing continues — same as server path |
| `watch` fallback, file arrives before engine ready | Engine started once before the loop; guaranteed healthy by the time files are processed |
| SIGINT / unclean exit | `atexit` handler terminates subprocess |
| `--model` flag when server is reachable | Silently ignored |

## Configuration

| Env var | Effect |
|---|---|
| `DEFAULT_FASTWHISPER_MODEL` | Model used by local engine (inherited from env / `.env`) |
| `ASR_MODE` | Transcriber backend (inherited) |
| `ENGINE_API_KEY` | Cleared for local subprocess (no auth needed locally) |

`--model <name>` on batch/watch overrides `DEFAULT_FASTWHISPER_MODEL` for the local subprocess only.

## What is NOT changing

- `ws-client` — remains a pure HTTP client, no subprocess logic
- `ws-engine` — no changes; runs as-is
- `ws-api` — no changes
- Normal server path in batch/watch — no behaviour change
- Job queue, replacements, prompts — not available in fallback mode (local engine is stateless)
