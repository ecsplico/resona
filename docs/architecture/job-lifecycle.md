# Job Lifecycle

This page traces what happens to a file from submission until the result is available. There are two paths: the hub path (`resona-api` plus pull workers) and the local path (CLI only, used only when no hub is configured).

## Hub path

```
Client → POST /v1/uploads (multipart file)            → {upload_id, sha256, size}
Client → POST /v1/jobs (Idempotency-Key; {kind, upload_id, ...})
  resona-api:
    derives the organization from the bearer token
    resolves settings, execution profile and postprocess profile revision
    checks policy before admission (private=true by default)
    stores an immutable execution_snapshot; returns 202 {job_id, status: "pending"}

resona-worker (outbound only, long-polls POST /v1/workers/claim):
  hub claims the oldest eligible pending job, mints a 120 s lease
  worker downloads input (GET /v1/workers/jobs/{id}/input, X-Resona-Lease)
  runs the engine in an isolated subprocess, heartbeats every 30 s
  asks /policy right before any external stage
  uploads JobResult (POST /v1/workers/jobs/{id}/result)

Client → GET /v1/jobs/{id}          → JobView {status: "completed", result_available: true}
Client → GET /v1/jobs/{id}/result   → {raw_text, markdown, segments, pages, provenance, ...}
```

### Job states

```
pending → processing → completed | failed | cancelled
```

| State | Meaning |
|-------|---------|
| `pending` | Waiting for a capable worker; `queue_reason` says why (`no_capable_worker`, `waiting_capacity`) |
| `processing` | Claimed by a worker under a lease |
| `completed` | Result accepted and stored |
| `failed` | Terminal error, `attempts_exhausted` (3 assignments) or `deadline_exceeded` (24 h) |
| `cancelled` | Cancelled by the client; the lease is fenced and later worker output is rejected |

If a lease expires (worker offline or asleep), the job returns to `pending` and another worker can claim it. A late result from the old lease gets `409 stale_lease`. Execution is at-least-once; the hub accepts one result.

Private jobs go only to workers that an admin approved with `resona-admin worker approve --private`. A worker cannot make itself private through its heartbeat. If no capable worker is online, the job waits; the hub never weakens privacy or swaps the model.

### Retention

Content is stored as files under `/data/organizations/<storage_id>/jobs/<job_id>/`, not in the database. A maintenance loop on the hub applies retention:

- input: deleted when the job reaches a terminal state
- result: deleted 24 hours after completion (`GET .../result` then returns `410`)
- job metadata: deleted after 7 days
- unattached uploads: deleted after 1 hour

## Local path

Only when no hub is configured (no `RESONA_API_URL` and no `resona-api` entry in `~/.resona/config.json`) does the CLI run the pipeline in-process. With a hub configured, hub errors (unreachable, timeout, policy denial) are reported and never fall back to a local or cloud engine; a policy denial exits with code 2.

```
resona transcribe ./audio/ --engine voxtral

  no hub configured

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
