# Local Engine Fallback — Design Spec

**Date:** 2026-03-30
**Status:** Approved

## Problem

`ws-cli batch` and `ws-cli watch` currently fail hard when no ws-api/ws-engine server is configured or reachable. Users with `ws-engine` installed locally should be able to transcribe files without standing up the full server stack.

## Chosen Approach

**New `LocalEngine` class in ws-cli** (`apps/cli/src/ws_cli/local_engine.py`).

`batch` and `watch` catch the `RuntimeError` from `WhisperClient.from_config()`, instantiate `LocalEngine` inside a `with` block, and call it directly — bypassing the job queue entirely. ws-client stays a pure HTTP client library.

## Required Pre-requisite: ws-engine `PORT` env support

`packages/ws-engine/src/ws_engine/run.py` currently hardcodes `port=7001`. It must be updated to read from env:

```python
port = config("PORT", default=7001, cast=int)
uvicorn.run(app, host="0.0.0.0", port=port, log_level=loglevel)
```

This is a minimal, non-breaking change. The default of 7001 preserves existing behaviour.

## Architecture & Components

### New file: `apps/cli/src/ws_cli/local_engine.py`

A `LocalEngine` context manager with the following constructor:

```python
LocalEngine(model: str | None = None, timeout: float = 120.0)
```

- `model`: if provided, overrides `DEFAULT_FASTWHISPER_MODEL` in the subprocess env.
- `timeout`: seconds to wait for the engine to become healthy.

**`__enter__`:**

1. **Finds a free local TCP port** by binding `socket(AF_INET, SOCK_STREAM)` to port 0, reading the assigned port, then closing the socket. The TOCTOU race is accepted as a known limitation for a local developer tool.
2. **Spawns `uv run ws-engine`** as a subprocess:
   - `env = os.environ.copy()` (inherits `DEFAULT_FASTWHISPER_MODEL`, `ASR_MODE`, etc.)
   - `PORT=<chosen_port>` injected
   - `ENGINE_API_KEY` removed
   - If `model` is set: `DEFAULT_FASTWHISPER_MODEL=<model>` injected, overriding any inherited value
   - `stdout=subprocess.DEVNULL` (uvicorn startup logs not needed — health polling drives readiness detection)
   - `stderr` redirected to a `tempfile.TemporaryFile` (avoids pipe-buffer deadlock from verbose model-load output)
3. **Polls `GET http://localhost:<port>/health`** using `httpx` until HTTP 200:
   - Checks `process.poll()` each iteration; if the process has exited, seeks to the start of the stderr temp file and raises `RuntimeError` with its contents.
   - Raises `RuntimeError` (with stderr contents) if health is not reached within `timeout` seconds.
   - Prints a dot per second to stderr while waiting.
4. **Registers `self._atexit_fn`** — a bound method stored before `atexit.register` is called — as a safety net. The handler calls `self._shutdown()` only if the process is still alive.

**`__exit__`:**

1. Calls `self._shutdown()` — `process.terminate()`, wait up to 10 s, then `process.kill()` if still alive.
2. Calls `atexit.unregister(self._atexit_fn)` to prevent double-invocation. (`atexit.unregister` unregisters all registrations of the given callable, so `self._atexit_fn` must be the same object that was passed to `atexit.register` — storing it as a bound method attribute before registering satisfies this.)
3. Closes the stderr temp file.

**Public method:**

```python
def transcribe(self, filepath: Path, language: str = "de") -> dict:
    """POST multipart audio to /transcribe. Returns {text, language, segments}.

    initial_prompt and replacements are omitted — no DB is available in
    local fallback mode. The response will not contain 'md'; callers
    should use the 'text' field.
    """
```

Uses `httpx`. Returns the full JSON response dict. Since no `replacements` are sent, the engine never includes `md` in the response; callers write the `text` field to disk.

### Changes to `run.py` in ws-engine

One-line change: read `PORT` from env (see pre-requisite section above).

### Changes to `batch.py`

**New options:**
- `--model` (str, default None) — local fallback only
- `--engine-timeout` (float, default 120s) — local fallback only
- `--language` (str, default `"de"`) — local fallback only; `submit_job` does not accept a language parameter, so this option is a no-op on the server path. Help text should say: "Language hint for transcription (local fallback only)."

**`--output-dir` already exists** — no change needed.

**Logic change:**

```python
try:
    client = WhisperClient.from_config()
    # existing server path unchanged
except RuntimeError:
    typer.echo("No server reachable — starting local engine "
               "(replacements, prompts, and language selection not available "
               "on server path).", err=True)
    with LocalEngine(model=model, timeout=engine_timeout) as engine:
        for filepath in files:
            try:
                result = engine.transcribe(filepath, language=language)
                transcript = result.get("text", "")
                out_path = (output_dir or filepath.parent) / f"{filepath.stem}.txt"
                out_path.write_text(transcript, encoding="utf-8")
                print(f"Transcribed {filepath.name} -> {out_path}")
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                typer.echo(f"Failed to transcribe {filepath.name}: {e}", err=True)
```

Note: the fallback path always writes a `.txt` file (to `filepath.parent` when no `--output-dir` is given), whereas the server path only writes when `--output-dir` is set. This asymmetry is intentional: in fallback mode there is no job to query later, so the result must be written immediately.

If `--model` is provided but a server **is** reachable: warn to stderr (`"--model is only used in local fallback mode and will be ignored."`) and proceed normally.

### Changes to `watch.py`

**New options:**
- `--model` (str, default None) — local fallback only
- `--engine-timeout` (float, default 120s) — local fallback only
- `--language` (str, default `"de"`) — local fallback only
- `--output-dir` (Path, default None) — new, consistent with `batch`

**Logic change:**

```python
try:
    client = WhisperClient.from_config()
    # existing server path in the watch loop unchanged
except RuntimeError:
    typer.echo("No server reachable — starting local engine "
               "(replacements and prompts not available).", err=True)
    with LocalEngine(model=model, timeout=engine_timeout) as engine:
        while True:
            for f in new_files:
                try:
                    result = engine.transcribe(f, language=language)
                    transcript = result.get("text", "")
                    out_path = (output_dir or f.parent) / f"{f.stem}.txt"
                    out_path.write_text(transcript, encoding="utf-8")
                    print(f"Transcribed {f.name} -> {out_path}")
                except (httpx.HTTPStatusError, httpx.RequestError) as e:
                    typer.echo(f"Failed to transcribe {f.name}: {e}", err=True)
            time.sleep(poll_interval)
```

The `with` block wraps the entire loop — `__exit__` is guaranteed to run on `KeyboardInterrupt`.

If `--model` is provided but a server is reachable: warn to stderr and proceed normally.

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
            → warn: "No server reachable — starting local engine..."
            → with LocalEngine(model, timeout) as engine:
                → find free port
                → spawn: uv run ws-engine (PORT=N, inherited env + overrides)
                → poll GET :N/health until 200 (or process death / timeout)
                → for each file:
                    POST :N/transcribe (multipart, audio bytes, language)
                    ← {text, language, segments}   # no md — no replacements sent
                    write <stem>.txt (text field) next to audio or into --output-dir
            → __exit__: terminate() → wait 10s → kill()
```

## Output Files

The `text` field is written to `<audio_stem>.txt`. Default location: same directory as the audio file. `--output-dir` overrides for both `batch` and `watch`. If `/transcribe` fails for a file, no `.txt` is written for that file.

**Asymmetry with server path:** `batch` on the server path only writes when `--output-dir` is given. The fallback always writes (to `filepath.parent` when no `--output-dir`). This is intentional — in fallback mode there is no persistent job to retrieve later.

## Error Handling

| Scenario | Behaviour |
|---|---|
| Subprocess fails to start (`uv run ws-engine` not found, bad model) | `LocalEngine.__enter__` raises `RuntimeError` with stderr contents; batch/watch print error and exit non-zero |
| Engine process dies before health check passes | `process.poll()` detected; `RuntimeError` raised immediately with stderr contents |
| Health poll timeout | `RuntimeError` with stderr contents after `--engine-timeout` seconds |
| `/transcribe` fails (`httpx.HTTPStatusError` or `httpx.RequestError`) | Error printed to stderr (`"Failed to transcribe <name>: <err>"`); no `.txt` written; processing continues |
| Engine subprocess dies during `watch` loop | Per-file `httpx.RequestError` caught and printed; loop continues (known limitation — user must restart) |
| `watch` fallback, file arrives before engine ready | Engine started before the loop; guaranteed healthy before any file is processed |
| `KeyboardInterrupt` | `with` block's `__exit__` runs: `terminate()` → wait 10 s → `kill()` |
| Unclean exit (SIGKILL to CLI process) | `atexit` handler not guaranteed to run; engine subprocess may be orphaned |
| `__exit__` runs cleanly | `atexit.unregister(self._atexit_fn)` prevents double-invocation |
| `--model` / `--language` when server is reachable | Warning to stderr; flags ignored |

## Configuration

| Env var | Effect |
|---|---|
| `DEFAULT_FASTWHISPER_MODEL` | Model used by local engine (inherited from env / `.env`) |
| `ASR_MODE` | Transcriber backend (inherited) |
| `ENGINE_API_KEY` | Removed from subprocess env (no auth needed locally) |
| `PORT` | Now read by `run.py` (new); `LocalEngine` injects a free port here |

`--model <name>` sets `DEFAULT_FASTWHISPER_MODEL=<name>` in the subprocess env, overriding any inherited value.

## HTTP Library

`LocalEngine` uses `httpx` directly (already a transitive dependency via ws-client). No new dependencies are introduced.

## Subprocess I/O

- `stdout`: `subprocess.DEVNULL` — uvicorn startup logs suppressed; readiness is detected via health polling.
- `stderr`: `tempfile.TemporaryFile` — buffered to disk to avoid pipe deadlock; read back on startup failure.

## Process Lifecycle (cross-platform)

Uses `subprocess.Popen.terminate()` and `subprocess.Popen.kill()` — the cross-platform `Popen` methods — rather than `signal` constants directly.

## Known Limitations

- Server/fallback selection is decided once at startup; if the server becomes reachable during a `watch` session the CLI does not switch back.
- If the engine subprocess crashes mid-`watch`, the session is not auto-recovered. User must restart.
- No replacements or prompts in fallback mode (local engine is stateless with no DB).
- Free-port selection has a theoretical TOCTOU race; negligible in practice on a developer machine.
- If the CLI process is killed with SIGKILL, the `atexit` handler does not run and the engine subprocess is orphaned.
- `--language` is only honoured in fallback mode; it is silently ignored on the server path (ws-api does not expose a language parameter on `POST /jobs`).
- If `WS_API_URL` is set in the environment, `from_config()` returns immediately without a reachability check — a dead server pointed to by `WS_API_URL` will not trigger the fallback; connection errors surface at transcription time instead.

## What is NOT changing

- `ws-client` — remains a pure HTTP client, no subprocess logic
- `ws-api` — no changes
- Normal server path in batch/watch — no behaviour change
- Job queue, replacements, prompts — not available in fallback mode

## Summary of Files Changed

| File | Change |
|---|---|
| `packages/ws-engine/src/ws_engine/run.py` | Read `PORT` from env (1 line) |
| `apps/cli/src/ws_cli/local_engine.py` | New file |
| `apps/cli/src/ws_cli/batch.py` | Add fallback path + new options |
| `apps/cli/src/ws_cli/watch.py` | Add fallback path + new options |
