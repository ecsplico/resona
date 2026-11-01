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
| `pending` | Waiting for a capable worker; `queue_reason` says why: `waiting_capacity` (an eligible worker is online and busy), `release_not_approved` (a worker offers one of the job's tuples, but its release has no promoted evaluation for the job's execution profile and platform) or `no_capable_worker` (no online worker of the job's privacy class offers one, e.g. the only CUDA worker runs without a usable GPU) |
| `processing` | Claimed by a worker under a lease |
| `completed` | Result accepted and stored |
| `failed` | Terminal error, `attempts_exhausted` (3 assignments) or `deadline_exceeded` (24 h) |
| `cancelled` | Cancelled by the client; the lease is fenced and later worker output is rejected |

If a lease expires (worker offline or asleep), the job returns to `pending` and another worker can claim it. A late result from the old lease gets `409 stale_lease`. Execution is at-least-once; the hub accepts one result.

Private jobs go only to workers that an admin approved with `resona-admin worker approve --private`; cloud opt-in jobs (`private: false`) go only to workers approved with `--non-private`, so a private execution environment never receives cloud work. A worker cannot change its approval through its heartbeat, and an OOM failure withdraws a tuple only on that worker, never its hub approval. A job whose endpoint is assigned to one worker (its secret file lives there) is claimed only by that worker. If no capable worker is online, the job waits; the hub never weakens privacy or swaps the model.

`DELETE /v1/jobs/{id}` in any state removes the job and its files at once; for a processing job it first fences the lease, so the worker's next call gets `409 stale_lease` and its output is rejected.

### Retention

Content is stored as files under `/data/organizations/<storage_id>/jobs/<job_id>/`, not in the database. A maintenance loop on the hub applies retention:

- input: deleted when the job reaches a terminal state
- result: deleted 24 hours after completion (`GET .../result` then returns `410`)
- job metadata: deleted after 7 days
- unattached uploads: deleted after 1 hour

## Local path

Only with an explicit `--local` does the CLI process on this machine; it is never chosen
automatically. Without `--local`, hub errors (unreachable, timeout, failed job) are reported and
never fall back to a local or cloud engine; a policy denial exits with code 2.

```
resona transcribe ./audio/ --local --execution-profile cpu-private

  Settings:
    cached revision from `resona settings pull` for this hub + credential (marked stale: true)
    or built-in private defaults

  For each audio file:
    resolve the execution profile's approved candidates (as hub admission does)
    check_policy on every stage  → policy_denied exits 2
    run the candidate in the worker's execution subprocess (resona_worker.execute)
      with offline model caches and no credentials
    postprocess (profile name, file or inline JSON; LLM stages policy-checked again)

  writes <stem>.md + <stem>.result.json  (or --output-dir; --force to overwrite)
```

!!! tip "No server, no database"
    In local mode there is no SQLite database and no job queue. The CLI processes files sequentially and writes the same outputs as hub jobs.
