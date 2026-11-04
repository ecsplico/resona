# resona-project-worker

Background worker that runs next to a CMS (Directus) and sends saved
**recordings** and uploaded **documents** through the Resona hub as durable jobs,
then writes the results back into Directus.

Per cycle, for `recordings` (hub job kind `audio`, active status `transcribing`)
and `documents` (kind `document`, active status `processing`):

1. **Reclaim:** active rows *without* `hub_job_id` older than
   `WORKER_STALE_MINUTES` go back to `pending` (a crash between claim and submit).
2. **Submit:** up to `WORKER_BATCH` `pending` rows (oldest first) are claimed,
   their file is downloaded, uploaded to `POST /v1/uploads`, and submitted to
   `POST /v1/jobs` (with an `Idempotency-Key`). The job id is stored in
   `hub_job_id`, so a restart resumes polling instead of submitting twice.
3. **Poll:** active rows with `hub_job_id` are checked via `GET /v1/jobs/{id}`.
   `completed` → the result is written back (recordings: a `transcripts` row from
   the result `markdown`; documents: `markdown` + `pages`), the row goes `done`,
   and the hub job is deleted. `failed`/`cancelled` → `error` with
   `Verarbeitung fehlgeschlagen: …`; a missing job → `Hub-Job nicht gefunden`;
   an expired result → `Ergebnis auf dem Hub abgelaufen`.

A temporarily unavailable hub (unreachable, timeout, HTTP 429/502/503/504) leaves
rows untouched; other per-row failures mark only that row as `error` and clear its
`hub_job_id`, so a retried row gets a fresh job. The state machine matches the PWA's
standalone processor
(`app/data/localProcessor.ts`).

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
