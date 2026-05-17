# Job Lifecycle

This page traces what happens to an audio file from the moment it is submitted until the transcript is available. There are two paths: the server path (via `resona-api`) and the local fallback path (CLI only, no server).

## Server path

```
Client → POST /jobs (multipart: audio file; optional profile=<name|inline JSON>)
  resona-api:
    saves file to FILE_PATH/
    creates Job(status=PENDING, profile=...) in SQLite
    returns job dict immediately (non-blocking)

TranscribeTask (background thread, polls every 1 s):
  finds oldest PENDING job
  sets status = PROCESSING
  resolves profile via resolve_profile(job.profile or "default", RESONA_PROFILES_DIR)
  calls EngineClient.transcribe(filepath, language, profile.initial_prompt_string())
    → POSTs multipart to engine POST /transcribe
    → engine loads audio via ffmpeg (pipe mode), runs inference
    → returns {text, language, segments}   ← raw, no replacements
  result = build_pipeline(profile).run(text)
  writes result.text as job.md
  writes result.data (structured JSON) as job.structured
  writes .md file to MD_PATH/
  sets status = COMPLETED (or FAILED on error)

Client → GET /job/{id} → {status: "completed", transcript: "...", md: "..."}
```

### Job states

```
PENDING → PROCESSING → COMPLETED
                     ↘ FAILED
```

| State | Meaning |
|-------|---------|
| `PENDING` | Job registered; waiting for `TranscribeTask` to pick it up |
| `PROCESSING` | Engine call in progress; postprocessing not yet applied |
| `COMPLETED` | Transcript and Markdown available; pipeline has run |
| `FAILED` | Unrecoverable error during engine call or postprocessing |

### TranscribeTask behaviour

`TranscribeTask` is a daemon thread started during the FastAPI lifespan event. It polls the SQLite database every second for the oldest `PENDING` job and processes one job at a time. The job's `profile` field (a name or inline JSON string) is resolved to a `Profile` object; if resolution fails the bundled `default` profile is used. If the engine call or postprocessing raises an exception, the job is marked `FAILED` and the loop continues with the next job.

### What is stored in each Job row

| Column | Type | Description |
|--------|------|-------------|
| `id` | int | Auto-incremented primary key |
| `audio_path` | str | Absolute path under `FILE_PATH/` |
| `status` | enum | `PENDING`, `PROCESSING`, `COMPLETED`, `FAILED` |
| `language` | str or null | Detected or requested language code |
| `engine` | str or null | Name of the engine that handled this job |
| `profile` | str or null | Profile name or inline JSON used for postprocessing (null = default) |
| `profile_config` | str or null | JSON snapshot of the resolved profile stored with the job |
| `transcript` | str or null | Raw text returned by the engine |
| `md` | str or null | Postprocessed Markdown output |
| `structured` | str or null | JSON output from any `extract` steps in the profile |
| `created_at` | datetime | Submission timestamp |
| `updated_at` | datetime | Last status-change timestamp |

!!! note "Audio files are never deleted"
    `resona-api` saves audio files to `FILE_PATH/` and never removes them. Neither the engine nor the postprocessing step deletes files. Retention is the operator's responsibility.

## Local fallback path

When no `resona-api` server is reachable (or when the user runs `resona transcribe` without configuring a server), the CLI handles the full pipeline in-process:

```
resona transcribe ./audio/ --engine voxtral

  server unreachable (or no server configured)

  Engine resolution:
    1. --engine flag → "voxtral"  (built-in local engine)
    2. (would fall back to config.json default_engine, then "faster-whisper")

  For each audio file:
    spawn: uv run resona-engine-voxtral on a free local port  (once, shared)
    wait for GET /health to return 200
    POST audio file to local engine POST /transcribe
    engine returns {text, language, segments}

  PostprocessPipeline:
    reads ~/.resona/postprocess.json  (or bundled defaults if absent)
    applies replacements + any LLM steps
    md = pipeline.run(text)

  writes transcript to <input>.txt  (or --output-dir if specified)
```

!!! tip "No server, no database"
    In local mode there is no SQLite database and no job queue. The CLI processes files sequentially and writes output files directly. Postprocessing still runs — it uses `~/.resona/postprocess.json` instead of DB-stored replacements.

### Engine resolution order

When the `--engine` flag is not given, `resona transcribe` resolves the engine in this order:

1. `--engine NAME` CLI flag — built-in local engine name, a `config.json` server entry, or a `config.json` cloud entry
2. `--private` / `--no-private` — when private is required (via flag or `default_private: true` in config), non-private and cloud engines are skipped
3. `default_engine` in `~/.resona/config.json`
4. Hardcoded default: `faster-whisper`
