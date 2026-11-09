# resona-project-worker

Background worker that runs next to a CMS (Directus) and sends saved
**recordings** and uploaded **documents** through the Resona hub as durable jobs,
then writes the results back into Directus.

Per cycle, for `recordings` (hub job kind `audio`, active status `transcribing`)
and `documents` (kind `document`, active status `processing`):

1. **Reclaim:** active rows *without* `hub_job_id` older than
   `WORKER_STALE_MINUTES` go back to `pending` (a crash between claim and submit),
   keeping their `hub_job_key`.
2. **Submit:** up to `WORKER_BATCH` `pending` rows (oldest first) are claimed,
   their file is downloaded, uploaded to `POST /v1/uploads`, and submitted to
   `POST /v1/jobs`. The `Idempotency-Key` is the row's `hub_job_key`, or a fresh
   `<collection>:<id>:<uuid>` written to `hub_job_key` in the claim PATCH, i.e.
   before the create call: if the hub accepted a job but the response was lost,
   the resubmission reuses the key and the hub returns the original job. The job
   id is stored in `hub_job_id`, so a restart resumes polling instead of
   submitting twice.
3. **Poll:** active rows with `hub_job_id` are checked via `GET /v1/jobs/{id}`.
   `completed` → the result is written back (recordings: a `transcripts` row from
   the result `markdown`; documents: `markdown` + `pages`), the row goes `done`,
   and the hub job is deleted. `failed`/`cancelled` → `error` with
   `Verarbeitung fehlgeschlagen: …`; a missing job → `Hub-Job nicht gefunden`;
   an expired result → `Ergebnis auf dem Hub abgelaufen`.

A temporarily unavailable hub (unreachable, timeout, HTTP 429/502/503/504) leaves
rows untouched; other per-row failures mark only that row as `error` and clear its
`hub_job_id` and `hub_job_key`, so a retried row gets a fresh job (and key); `done`
clears both too. The state machine matches the PWA's
standalone processor
(`app/data/localProcessor.ts`).

### Re-processing transcripts ("Nachbearbeiten", hub job kind `text`)

A third pass runs over `transcripts` rows whose `postprocess_status` the PWA set to
`pending` (with `postprocess_profile`). Same reclaim/submit/poll rules, on the
`postprocess_*` fields (active status `processing`, job in `postprocess_job_id`, key
in `postprocess_job_key`): the raw transcript (`raw_text`, else `text`) is uploaded as
`transcript.md` and submitted as `POST /v1/jobs {kind: "text", profile}`. On
`completed`, `text` becomes the result `markdown`, `raw_text` keeps the raw
transcript, `applied_profile` the profile and `processed_text` the output. While the
job waits, its `queue_reason` (e.g. `no_capable_worker` when the worker serving a
`local-llm` profile is offline) is written to `postprocess_queue_reason`. Hub errors
`profile_required`, `profile_not_found`, `invalid_profile` and `policy_denied` (with
the hub's reason) become a German `postprocess_error`; text and raw transcript stay
untouched on failure. A `pending` row that still has a `postprocess_job_id` was
re-requested while that job ran: the old job is deleted on the hub and the new one
gets a fresh key. A new audio transcript (`write_transcript`) clears every
`postprocess_*` field and deletes a text job still running on the old one.

## Configuration

| Env | Default | |
|-----|---------|-|
| `DIRECTUS_URL` | `http://localhost:7700` | |
| `DIRECTUS_TOKEN` | — | required; static token of the bootstrap's worker user |
| `RESONA_API_URL` | `http://localhost:7000` | hub base URL |
| `RESONA_API_KEY` | — | required; org-bound hub **client** token |
| `WORKER_ENGINE` | empty | audio engine per recording job; empty = hub default |
| `WORKER_POLL_INTERVAL` | `5` | seconds between cycles |
| `WORKER_BATCH` | `5` | max pending rows submitted per collection per cycle |
| `WORKER_STALE_MINUTES` | `15` | reclaim threshold for active rows without a job |
| `LOGLEVEL` | `info` | |

## Development

    uv sync
    uv run pytest
    uv run resona-project-worker
