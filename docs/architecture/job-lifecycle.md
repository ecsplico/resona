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
    hub: job → completed (+ webhook outbox row, same transaction)

hub webhook dispatcher (only for jobs created with `webhook`):
  POST signed {type, job_id, kind, status, error_code, finished_at} → endpoint URL

Client → GET /v1/jobs/{id}          → JobView {status: "completed", result_available: true}
Client → GET /v1/jobs/{id}/result   → {raw_text, markdown, segments, pages, provenance, ...}
```

### Job states

```
pending → processing → completed | failed | cancelled
```

| State | Meaning |
|-------|---------|
| `pending` | Waiting for a capable worker; `queue_reason` says why: `waiting_capacity` (an eligible worker is online and busy), `release_not_approved` (a worker offers one of the job's tuples, but its release has no promoted evaluation for the job's execution profile and platform), `worker_outdated` (such a worker's release is approved but does not know an option the job sets, e.g. a 0.1.0 worker and `remove_repeated_headers`) or `no_capable_worker` (no online worker of the job's privacy class offers one, e.g. the only CUDA worker runs without a usable GPU, or a `diarize` job and no such worker reports a verified diarizer) |
| `processing` | Claimed by a worker under a lease |
| `completed` | Result accepted and stored |
| `failed` | Terminal error, `attempts_exhausted` (3 assignments) or `deadline_exceeded` (24 h) |
| `cancelled` | Cancelled by the client; the lease is fenced and later worker output is rejected |

If a lease expires (worker offline or asleep), the job returns to `pending` and another worker can claim it. A late result from the old lease gets `409 stale_lease`. Execution is at-least-once; the hub accepts one result.

Private jobs go only to workers that an admin approved with `resona-admin worker approve --private`; cloud opt-in jobs (`private: false`) go only to workers approved with `--non-private`, so a private execution environment never receives cloud work. A worker cannot change its approval through its heartbeat, and an OOM failure withdraws a tuple only on that worker, never its hub approval. A job whose endpoint is assigned to one worker (its secret file lives there) is claimed only by that worker. If no capable worker is online, the job waits; the hub never weakens privacy or swaps the model.

`DELETE /v1/jobs/{id}` in any state removes the job and its files at once; for a processing job it first fences the lease, so the worker's next call gets `409 stale_lease` and its output is rejected.

### Webhook outbox

A job admitted with `webhook` (an endpoint name, production jobs only) records that endpoint. Every transition into a terminal state writes the job's event into the outbox table `WebhookDelivery` in the **same transaction** that changes the status: the accepted result (`completed`), a worker failure or a lease-sweep failure such as `attempts_exhausted` or `deadline_exceeded` (`failed`), a cancel or a delete of a pending or processing job (`cancelled`). So a job never ends without its event, and an event never exists for a status change that was rolled back. A lease expiry that requeues the job is not terminal and writes nothing; neither does purging a deleted job's tombstone later. An endpoint revoked before the job ends gets no row.

Sending happens afterwards and outside that transaction: the hub's dispatcher loop polls the outbox about once a second, signs each due row (Standard Webhooks headers) and POSTs it, retrying failures with backoff until a `2xx`, 24 hours or revocation of the endpoint. A slow or unreachable receiver therefore never delays or blocks a job transition, and a hub restart resumes pending deliveries (at least once, same `webhook-id`). The payload carries identifiers only; the result stays behind `GET /v1/jobs/{id}/result`. Receiver side: [Webhooks guide](../guide/webhooks.md); wire contract: [HTTP API](../reference/api.md#webhook-endpoints-role-org_admin).

### Retention

Content is stored as files under `/data/organizations/<storage_id>/jobs/<job_id>/`, not in the database. A maintenance loop on the hub applies retention:

- input: deleted when the job reaches a terminal state
- result: deleted 24 hours after completion (`GET .../result` then returns `410`)
- job metadata: deleted after 7 days
- unattached uploads: deleted after 1 hour

[Testbench](../guide/testbench.md) items are the exception: the original and the run results are stored under `<storage_id>/testbench/` and are not touched by these sweeps. A collector copies each testbench job's result into its run file before the 24 h result retention, so the job row and result may expire while the run keeps its data. Testbench jobs have `purpose=testbench` and need no promoted release tuple.

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
