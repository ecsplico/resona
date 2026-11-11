# resona-api

resona-api is the central hub. It accepts uploads, keeps a durable, organization-scoped job queue and hands jobs to outbound-only pull workers (`resona-worker`). The hub runs no inference for queued jobs. It also exposes OpenAI-compatible `/v1/audio/*` routes.

- Default port: **7000**
- Database: SQLite at `$DATA_PATH/resona.db` (single hub replica)
- Upload limit: 200 MiB per upload (`RESONA_MAX_UPLOAD_BYTES`), enforced while streaming; 2 MiB for text uploads (`RESONA_MAX_TEXT_UPLOAD_BYTES`)

## Authentication

Every request needs an org-bound credential: `Authorization: Bearer <token>`. REST routes also accept `X-API-Key: <token>`; sending both with different values is rejected. Missing, revoked or unknown token → `401`; wrong role → `403`. There is no auth-disabled mode.

The organization always comes from the credential, never from a request field or header. IDs that belong to another organization return `404`.

Issue tokens on the hub host with `resona-admin`. Each token is printed once; the hub stores only its SHA-256 hash.

```bash
resona-admin organization create acme
resona-admin credential issue --organization acme --role client     # or org_admin, hub_operator
resona-admin credential revoke <credential-id>
resona-admin worker enroll mac-studio --organization acme           # worker token, unapproved
resona-admin worker approve <worker-id> --private --capabilities-file caps.json
resona-admin worker revoke <worker-id>
resona-admin restore <backup.sqlite>                                # hub stopped; see docs/server/coolify.md
```

Roles: `client` (submit and read jobs, read settings), `org_admin` (also change settings and profiles, manage its own organization through `/v1/admin`), `hub_operator` (manages every organization through `/v1/admin` and `/admin`; no job content, settings writes or promotion), `worker` (worker routes only).

Hub operator credentials are issued only with `resona-admin` on the server, never over HTTP. They still need `--organization` (a home organization); the role itself grants cross-organization access.

Error bodies have the form `{"error": "<code>", "message": "<safe text>"}`.

## Endpoints

### Health and readiness (no auth)

```
GET /health
GET /ready
```

`/health` is liveness only and always returns `{"status": "ok"}` while the process serves HTTP.

`/ready` returns `200` when the schema is at the target version, the data and database directories are writable and the maintenance loop finished a pass within the last 90 seconds; otherwise `503` with `status: not_ready`. `capacity` is `available` when an approved worker was seen recently and `degraded` otherwise. Offline workers never fail readiness: jobs wait in the queue.

```json
{"status": "ready", "checks": {"schema": true, "data_writable": true, "maintenance": true}, "capacity": "degraded"}
```

---

### Uploads and jobs (roles `client`, `org_admin`)

Jobs are asynchronous: upload the file, create a job, then poll until it finishes. `ResonaClient.submit_job()` does the upload and the job creation in one call.

#### Upload a file

```
POST /v1/uploads
Content-Type: multipart/form-data   (field: file)
```

Returns `201 {"upload_id", "sha256", "size"}`. Empty file → `422 empty_upload`, unsupported media → `415 unsupported_media`, over 200 MiB → `413 too_large`. Both limits are enforced while streaming: the upload stops at the limit and nothing is kept. An upload that is not attached to a job expires after one hour. One upload can be used by exactly one job.

Audio and document media are recognized by their magic bytes. Documents are PDF, PNG, JPEG, TIFF and Word DOCX (`application/vnd.openxmlformats-officedocument.wordprocessingml.document`). ZIP magic alone is not enough for a DOCX: the package must contain `word/document.xml` and a `[Content_Types].xml` that declares the Word main part. Other ZIP-based Office formats (`.docm`, `.dotx`, `.xlsx`, `.pptx`, `.odt`) and legacy `.doc` → `415 unsupported_media`. A file that is neither, but is valid UTF-8 without NUL bytes (`.md`, `.txt`), is stored as a `text` upload for a [text job](#text-jobs); a text upload over `RESONA_MAX_TEXT_UPLOAD_BYTES` (default 2 MiB) → `413 too_large` as soon as the stream passes that size (the same applies to upload links). `POST /v1/audio/transcriptions` accepts media only.

#### Upload through a single-use link

For a caller that must not hold the client credential, for example an agent sandbox behind the MCP server. The file goes to the hub with `curl`, so its bytes never pass through a model.

```
POST /v1/upload-links
```

Returns `201 {"upload_url", "token_header", "token", "expires_at", "max_bytes"}`. The hub stores only the token's SHA-256. `upload_url` uses `RESONA_PUBLIC_URL` when it is set; behind a proxy, set it to the public origin.

```
POST /v1/upload-links/upload
X-Resona-Upload-Token: <token>
Content-Type: multipart/form-data   (field: file)
```

Same response and errors as `POST /v1/uploads`; the upload belongs to the organization that minted the link. The link is valid for ten minutes and is spent by the first attempt, even one that fails validation. An unknown, used or expired link, or one whose minting credential was revoked, gets `401 invalid_upload_link`. Maintenance deletes expired links.

#### Create a job

```
POST /v1/jobs
Idempotency-Key: <unique key>
Content-Type: application/json
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `kind` | string | yes | `audio`, `document` or `text` (must match the upload, else `422 kind_mismatch`) |
| `upload_id` | string | yes | ID returned by `POST /v1/uploads` |
| `execution_profile` | string | no | Execution profile from the organization settings |
| `language` | string | no | Language code; defaults to the organization setting |
| `engine`, `model` | string | no | Override; must be an approved candidate (else `422`) |
| `profile` | string | no (yes for `text`) | Name of a postprocess profile stored on the hub |
| `instructions` | string | no | 1–8000 characters of user instructions for the profile's `llm`/`extract` steps (see [User instructions](#user-instructions)). Without a profile that has such a step → `422 invalid_instructions` |
| `options` | object | no | Audio: `translate`, `word_timestamps`, `vad_filter`, `diarize` (label speakers; result segments carry `speaker`, only workers that diarize claim the job). Document: see [Document options](#document-options) |
| `private` | bool | no | Default `true`. `false` needs organization cloud approval, else `403 policy_denied` |
| `provenance` | object | no | `project_id`, `session_id`, `source` (informational only) |

Returns `202 {"job_id": 42, "status": "pending"}`. The `Idempotency-Key` header is required: the same key with the same request returns the same job; the same key with a different request returns `409 idempotency_conflict`.

##### Text jobs

A `text` job runs only a postprocess profile on an uploaded text, without an engine: use it to re-run a profile on an existing transcript (for example a new profile, or the local LLM only its assigned worker reaches). `profile` is required (`422 profile_required`); `execution_profile`, `engine` and `model` are rejected (`422 invalid_request`), and so is `purpose: evaluation`. `options` must be empty. Admission checks the profile's stages against the organization policy as for every job (`403 policy_denied`), and the snapshot records the profile revision and policy. The job's `execution_profile` is reported as `-`, a name settings reject for real execution profiles, and `candidate` stays `null`.

Any approved worker of the job's privacy class that reports text support claims it, as long as every worker-assigned endpoint of the profile is assigned to that worker and its release is one the hub knows (staged, or the hub's bundled release). Text jobs need no promoted evaluation: approval measures engine output on the evaluation corpus, and a text job runs no engine (postprocess output is never gated); the code they run is the postprocess path every audio and document job already runs on that release. A worker on a release the hub never saw (a dev build, an unfinished update) gets no text jobs.

##### User instructions

Each `llm`/`extract` step sends its `prompt` as the **system** message. `instructions` (on the job, or as a default in the stored profile) are sent in the **user** message, ahead of the text:

```text
<instructions>
Keep the name "Erika Mustermann". Write doses as "5 mg".
</instructions>

<text>
…chunk…
</text>
```

If the model wraps its whole reply in `<text>…</text>`, the tags are removed. A job's `instructions` replace the profile's. Steps without instructions send only the text, as before. The instructions are written into the job's postprocess artifact (the profile document the worker downloads under its lease; its SHA-256 is in the snapshot). They never go into the content-free `execution_snapshot` and are deleted with the job's other files.

Guards: only chunked `llm` steps with `max_length_deviation` / `preserve_markup` compare each reply with its chunk. A step without those guards, and every `extract` step, follows the instructions unchecked; give steps that must not rewrite content these guards.

Workers must run a release with instruction support. A worker of an older release ignores the unknown `instructions` key and runs the profile without them, without an error; deploy the release to every worker before sending instructions.

##### Document options

All fields are optional; an unset field is resolved by the execution profile and engine. Unknown fields → `422`.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `languages` | list | `["de", "en"]` | Non-empty; each of `de`, `en`, `fr`, `it`, `es`, `ko`. Translated per OCR engine (ocrmac `de-DE`…`ko-KR`, tesseract `deu`/`eng`/`fra`/`ita`/`spa`/`kor`) |
| `ocr` | string | `auto` | `auto` (OCR bitmap regions only, digital text kept), `force` (full-page OCR), `off` |
| `ocr_engine` | string | profile candidate | `ocrmac`, `rapidocr` or `tesseract` |
| `page_start`, `page_end` | int ≥ 1 | all pages | Inclusive 1-based range; `page_start` ≤ `page_end` |
| `pipeline` | string | `standard` | Only `standard` |
| `image_scale` | float 1.0–4.0 | `1.0` | Multiplies Docling's OCR raster: OCR crops are rendered at `3.0 × image_scale` × 72 dpi (216 dpi at `1.0`). Also sets Docling's `images_scale` for rendered page images, which Resona does not export. Requires OCR |
| `ocr_min_confidence` | float 0–1 | engine default | Recognizer confidence threshold. Only `rapidocr` (`text_score`, default `0.5`). Requires OCR |
| `remove_repeated_headers` | bool | `false` | Drops text blocks repeated in the top (20%) or bottom (15%) band of at least half the pages (letterheads, patient header lines, fax headers, footers); the first occurrence stays and `repeated_header_blocks_removed:<n>` is added to `warnings`. Also valid with `ocr: off` |

Combinations no worker can run are rejected at submission with `422 invalid_options` instead of being ignored:
`ocr_min_confidence` or an `image_scale` other than `1.0` with `ocr: off`; `ocr_min_confidence` with
`ocrmac`/`tesseract` (no threshold exists); `ko` with `rapidocr` (the pinned rapidocr bundles only the
multilingual PP-OCRv6 recognizer, which serves `de`/`en`/`fr`/`it`/`es`; Korean weights would be downloaded at
job time, which jobs never do). The OCR-engine rules are checked per document candidate of the execution profile
(using the job `language` when `languages` is unset): the job snapshot keeps only the candidates that can run the
options, and `422` follows only when none can. For example, `ocr_min_confidence` on `private-auto` leaves only its
`rapidocr` candidate, and `ko` only its `ocrmac` candidate. A tesseract language whose pack is not installed on a
worker is `unsupported_capability`: that worker stops offering the candidate and the job is requeued.

With OCR on, the worker turns every page upright first (Tesseract orientation detection, `osd` traineddata, required
for every OCR engine; a worker without it answers `unsupported_capability`). Each turned page adds the result warning
`page_rotated:<page>:<clockwise degrees>`; see [CLI: page orientation](../guide/cli.md#page-orientation).

**DOCX.** A Word document goes through Docling's Word backend, which reads the document XML: no layout
model, no OCR, no orientation pass, no pages. Headings, lists and tables are kept (Word's Heading 1 becomes
`##`, because Docling reserves `#` for a document title); embedded pictures become `<!-- image -->`
placeholders as in PDFs. The result has `pages: []` and `language: null`. Options that act on pages or OCR
are rejected at submission with `422 invalid_options` rather than ignored: `ocr` (except `off`),
`ocr_engine`, `page_start`, `page_end`, `image_scale`, `ocr_min_confidence`, `remove_repeated_headers`
(except `false`). `languages` and `pipeline` are accepted. Because no OCR runs, a DOCX job is not filtered
by OCR engine: every docling candidate of the execution profile can run it, whatever its `ocr_engine` and
the job language. There is no DOCX-only worker capability: a worker serves DOCX with its approved docling
tuples, and `resona-worker prepare` approves a docling tuple only when it converts both the scanned PDF
fixture (with OCR, so orientation detection is needed) and a Word fixture.

#### List, get, cancel, delete

```
GET    /v1/jobs?cursor=<opaque>&limit=<1..100>   → {items: [JobView], next_cursor}
GET    /v1/jobs/{id}                             → JobView
GET    /v1/jobs/{id}/result                      → JobResult (409 not_ready, 410 expired)
GET    /v1/jobs/{id}/result/markdown?offset=&limit=&source=  → MarkdownChunk (409 not_ready, 410 expired)
POST   /v1/jobs/{id}/cancel                      → JobView
DELETE /v1/jobs/{id}                             → 204
```

JobView fields: `job_id`, `kind`, `status`, `queue_reason` (null unless `pending`), `private`, `execution_profile`, `candidate`, `attempt`, `error` (`{code, message}` or null), `result_available`, `created_at`, `updated_at`, `deadline_at`.

`queue_reason` of a pending job:

| Value | Meaning |
|-------|---------|
| `no_capable_worker` | No online worker of the job's privacy class (private jobs: `--private` workers; `private: false` jobs: non-private workers) offers one of the job's candidates (text jobs: runs text jobs; `diarize` jobs: also reports `diarize`) with the job's worker-assigned endpoints |
| `release_not_approved` | Such a worker is online, but its release is not approved for the job's (execution profile, platform): promote an evaluation of that release. Text jobs: the worker's release is neither staged nor the bundled release; stage it |
| `waiting_capacity` | An eligible worker is online and busy |

`GET /v1/jobs/{id}/result/markdown` returns one character range of the result's Markdown, never the whole result: `{text, offset, next_offset, total_chars, source, kind, language, page_count, warnings, provenance}`. `offset` ≥ 0 (default 0); `limit` 1–32768 characters (default 32768, larger → `422`). `source` is `markdown` (default) or `raw`: `raw` reads `raw_markdown` (the text before postprocessing) when the result has one and `markdown` otherwise; the response's `source` names the field read (`markdown` or `raw_markdown`). `next_offset` is `null` once the range reaches the end; an offset past the end returns empty `text`. Concatenating `text` from offset 0 until `next_offset` is `null` reproduces that field exactly.

`DELETE /v1/jobs/{id}` works in every state. On a pending or processing job it also cancels the job: the lease is fenced, so the worker's next lease call returns `409 stale_lease` and its result is rejected. The job, its idempotency keys, input and result files are gone at once; every later client read (`GET`, result, cancel, list, delete) answers `404`. The hub keeps an invisible tombstone of a deleted processing job only until its lease would have expired, to fence the worker.

JobResult fields: `schema_version`, `kind`, `raw_text`, `markdown`, `raw_markdown`, `language`, `segments`, `pages`, `warnings`, `structured`, `provenance`. `markdown` is the final text. `raw_markdown` is optional (`null` or absent): it holds the engine's Markdown before postprocessing and is set only when a postprocess step changed it. For documents `raw_text` is the converter's plain text without tables, so `raw_markdown` is the unpolished reference. For audio `raw_text` is the engine's plain transcript; the worker builds `markdown` from `segments`: segment texts joined with spaces, a blank line between paragraphs. A paragraph ends at a pause of 1.5 s or more between one segment's `end` and the next `start`, or, once it is 1000 characters long, after the next segment ending a sentence (`.`, `!`, `?`, `…`, optionally followed by a closing quote or bracket); once it reaches 2000 characters, at the next segment. An engine without segment text yields `markdown` = `raw_text`. Postprocess profiles work on this Markdown, so with a profile `markdown` is the profile's output and `raw_markdown` the paragraphed transcript. For `text` jobs `raw_text` is the uploaded text, `markdown` the profile output, `raw_markdown` the uploaded text when the profile changed it, `language` the job's language; `segments` and `pages` stay empty and provenance has no engine stage. Results without the field stay valid; a hub older than this field rejects results that carry it (`422 invalid_result`), so update the hub before its workers.

#### Job state machine

```
pending → processing → completed | failed | cancelled
```

A worker claims a job under a 120-second lease. If the lease expires, the job returns to `pending`. A job fails after three assignments (`attempts_exhausted`) or 24 hours (`deadline_exceeded`). Poll `GET /v1/jobs/{id}` or use `ResonaClient.wait_for_job()`.

Retention: the input is deleted when the job reaches a terminal state, results after 24 hours, job metadata after 7 days.

---

### Worker routes (role `worker`)

Used only by `resona-worker`. Every call checks the organization, that the worker is approved and not revoked, and the current policy. A worker claims only jobs of its privacy class: private jobs go only to workers approved with `--private`, `private: false` (cloud opt-in) jobs only to workers approved with `--non-private`. A job whose postprocess or cloud ASR endpoint is assigned to a worker (`worker_id` in the endpoint registry) is claimable only by that worker, and `POST …/policy` denies (`403 policy_denied`) an endpoint assigned to another worker. Production jobs need a worker release approved for the job's (execution profile, platform) by a promoted evaluation; no release, including the hub's own, is approved without one. A claim poll never modifies jobs it cannot take; the maintenance sweep fails non-private jobs whose organization has since disabled cloud processing (`policy_denied`).

```
POST /v1/workers/heartbeat
POST /v1/workers/claim?wait=<0..25>
GET  /v1/workers/jobs/{id}/input        (X-Resona-Lease)
POST /v1/workers/jobs/{id}/heartbeat    (X-Resona-Lease)
POST /v1/workers/jobs/{id}/policy       (X-Resona-Lease)
POST /v1/workers/jobs/{id}/result       (X-Resona-Lease, X-Resona-Result-Sha256)
POST /v1/workers/jobs/{id}/fail         (X-Resona-Lease)
```

A call with an expired or replaced lease, or on a cancelled or deleted job, returns `409 stale_lease`. An incompatible worker protocol returns `426`. `withdraw_capability` in a `fail` body is recorded in the job's error message only: the hub never changes a worker's approved capabilities; the worker itself stops offering the withdrawn tuple until it is prepared again.

Heartbeat and claim bodies carry `text_jobs: true` from workers that run [text jobs](#text-jobs) (every current `resona-worker`; no capability tuple is needed, so a worker whose tuples are all unusable still polls for them). A claim without it never receives a text job, so older workers keep working unchanged.

Likewise `diarize: true` comes only from workers whose `prepare` verified the speaker diarizer. An audio job with `options.diarize` is offered only to a claim carrying it; other claims skip it without touching the job. The hub stores the flag per worker (`Worker.diarize`, schema migration 7) for `queue_reason`. A false `diarize` is left out of the execution snapshot, so workers predating the option still run every other audio job.

---

### Settings

```
GET /v1/settings      → {organization_id, revision, settings} + ETag
PUT /v1/settings      (org_admin, If-Match: <revision>)
```

---

### Management (`/v1/admin`, roles `org_admin`, `hub_operator`)

An org admin manages only its own organization: any other `{org}` returns `404 organization_not_found`. A hub operator manages every organization. Worker and credential IDs of another organization return `404` (`worker_not_found`, `credential_not_found`), never `403`. Other roles get `403`.

```
GET  /v1/admin/me                                        → {role, organization_id, credential_id}
GET  /v1/admin/organizations                             → {items: [{id, created_at}]}  (org admin: own only)
POST /v1/admin/organizations          {id}               → 201 {id, created_at}  (hub_operator only; 409 conflict if it exists)
GET  /v1/admin/organizations/{org}/workers              → {items: [Worker]}  (active first, then by name)
POST /v1/admin/organizations/{org}/workers/{id}/approve {private: bool} → Worker
POST /v1/admin/organizations/{org}/workers/{id}/revoke  → Worker  (idempotent)
GET  /v1/admin/organizations/{org}/credentials          → {items: [{id, role, worker_id, created_at, revoked_at}]}  (newest first)
POST /v1/admin/organizations/{org}/credentials  {role: "client"|"org_admin"} → 201 {id, role, token}
POST /v1/admin/organizations/{org}/credentials/{id}/revoke → credential item
GET  /v1/admin/organizations/{org}/jobs?cursor=&limit=  → same page as GET /v1/jobs (metadata only)
GET  /v1/admin/organizations/{org}/settings             → {revision, settings}  (read-only)
GET  /v1/admin/organizations/{org}/baselines            → {items: [...], approved_releases: [...]}
```

Worker fields: `id`, `name`, `approved`, `approved_private`, `online` (seen within 90 s and not revoked), `last_seen`, `runtime_version`, `revoked`, `reported_capabilities`, `approved_capabilities`. Approval grants exactly the worker's reported capabilities: none reported → `409 no_reported_capabilities` (run the worker once after `resona-worker prepare`); a revoked worker → `409 conflict`. Partial approval stays with `resona-admin worker approve --capabilities-file`.

An issued token appears only in the `201` response; listings never contain tokens or hashes. Any other role in the body (including `hub_operator`) → `422`. Revoking your own credential → `409 cannot_revoke_self`; a worker credential → `409 use_worker_revoke` (revoke the worker instead).

Baselines: one item per (execution profile, platform) with its highest revision, `{execution_profile, platform, revision, evaluation_id, release_id, corpus_sha256, created_at}`; `approved_releases` items are `{execution_profile, platform, release_id, evaluation_id, approved_at}`.

There are no admin routes for job results, cancel or delete: org admins cancel and delete through `/v1/jobs/...`; operators see job metadata only.

#### Web page

`GET /admin` serves a static management page (no build step at runtime, no external assets). Sign in with an org admin or hub operator token; the page keeps it in `sessionStorage` (`resona-admin-token`), so it is gone when the tab closes. Sections (hash routes such as `#/workers`): Overview, Workers, Credentials, Jobs, Settings, Baselines, Testbench. The page is built with [IBM Carbon](https://carbondesignsystem.com) web components (Apache-2.0) and IBM Plex fonts (OFL), vendored as one prebuilt bundle into the hub package and served from `/admin/vendor/carbon/` (`carbon.js`, `carbon.css`, `fonts/*.woff2`; rebuilt with `packages/api/admin-vendor/build.sh`); no CDN, external fonts or icon kits are loaded. Responses carry a strict `Content-Security-Policy` (`default-src 'none'`, same-origin scripts, styles, fonts and requests only; `blob:` images and media for testbench previews), `Referrer-Policy: no-referrer` and `X-Content-Type-Options: nosniff`. `/admin`, `/admin/admin.js` and `/admin/admin.css` are sent with `Cache-Control: no-store`; the vendored bundle (`.js`, `.css` and `.woff2` files only; `.js`/`.css` as their gzip twin when the client accepts gzip) with `Cache-Control: public, max-age=86400`. Any other `/admin/…` path, including paths that leave the vendor directory, → `404`.

---


### Engine catalogue

#### List engines

```
GET /v1/engines
```

Returns the engines the caller's organization can use. Nothing is probed over the network. An audio/document engine appears when a candidate tuple of one of the organization's execution profiles is both approved for and reported as tested (prepared) by at least one approved, unrevoked worker of the organization. Cloud ASR candidates (deepgram, openai, elevenlabs) appear only when the organization has `cloud_enabled=true` and an approved `cloud-asr:<provider>` endpoint, and only through a non-private worker. Synchronous TTS engines of `POST /v1/audio/speech` follow the same cloud rule.

Response:

```json
{
  "engines": [
    {
      "name": "mlx-whisper",
      "kind": "local",
      "job_kind": "audio",
      "capabilities": ["stt"],
      "private": true,
      "available": true,
      "models": ["mlx-community/whisper-large-v3-mlx"],
      "candidates": [
        {
          "candidate": {"engine": "mlx-whisper", "model": "mlx-community/whisper-large-v3-mlx",
                        "model_revision": null, "accelerator": "metal", "ocr_engine": null},
          "execution_profiles": ["mac-private", "private-auto"]
        }
      ]
    }
  ],
  "default": "mlx-whisper"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Engine identifier used in `engine` fields |
| `kind` | string | `"local"` (runs on a worker) or `"cloud"` (provider) |
| `job_kind` | string | `audio`, `document` or `speech` (TTS) |
| `capabilities` | string[] | `"stt"`, `"document"` or `"tts"` |
| `private` | bool | `true` for local engines; cloud engines are always `false` |
| `available` | bool | A worker offering it is online now (TTS: provider key set) |
| `models` | string[] | Model names of the listed candidates |
| `candidates` | object[] | Each approved tuple and the execution profiles that contain it |

`default` is the engine of the first listed audio candidate of the organization's default execution profile, or `null`.

---

### Audio (v1 — OpenAI-compatible)

#### Transcribe

```
POST /v1/audio/transcriptions
Content-Type: multipart/form-data
```

OpenAI-compatible speech-to-text, implemented as an adapter onto the job path. The hub never runs
STT itself: it creates an audio job with the same admission and policy checks as `POST /v1/jobs`,
then waits up to `RESONA_SYNC_WAIT_SECONDS` (default `60`) for a worker to finish it. An optional
`Idempotency-Key` header works as for `POST /v1/jobs`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | file | yes | Audio file |
| `model` | string | no | `whisper-1` uses the execution profile's default; anything else must be an approved candidate |
| `language` | string | no | Language hint (default: the organization's setting) |
| `response_format` | string | no | `"json"` (default), `"text"`, or `"verbose_json"` |
| `prompt` | string | no | Accepted and ignored |
| `temperature` | float | no | Accepted and ignored |
| `engine` | string | no | Engine override; must be an approved candidate of the execution profile |
| `execution_profile` | string | no | Execution profile (default: the organization's default) |
| `profile` | string | no | Stored organization profile name; omit to use the organization default |
| `private` | bool | no | Default `true`; `false` needs `cloud_enabled=true` for the organization |
| `diarize` | bool | no | Default `false`; `true` labels speakers (`AudioOptions.diarize`): `verbose_json` segments carry `speaker`, the text has `**Sprecher N:**`/`**Speaker N:**` paragraphs |

Errors:

- An override that matches no approved candidate: `422 unapproved_override`.
- A failed job returns its error code: `403 policy_denied`, otherwise `502`.
- Timeout: `504` with `Location: /v1/jobs/<job_id>`; the job keeps running and its result can be
  fetched from the job routes.

```json
{"error": "timeout", "message": "...", "job_id": 42}
```

With the Python client, `ResonaClient.create_transcription(...)` raises `SyncWaitTimeout` (with
`.job_id`) on `504`.

`response_format` responses:

```json
// json (default)
{"text": "Das ist ein Test."}

// text
Das ist ein Test.

// verbose_json
{
  "text": "Das ist ein Test.",
  "language": "de",
  "duration": 3.2,
  "segments": [{"start": 0.0, "end": 3.2, "text": "Das ist ein Test."}]
}

// verbose_json with diarize=true: each segment is one speaker section
{
  "text": "**Sprecher 1:** Guten Tag.\n\n**Sprecher 2:** Hallo.",
  "language": "de",
  "duration": 2.4,
  "segments": [
    {"start": 0.0, "end": 1.1, "text": "Guten Tag.", "speaker": 1},
    {"start": 1.1, "end": 2.4, "text": "Hallo.", "speaker": 2}
  ]
}
```

#### Synthesise speech

```
POST /v1/audio/speech
Content-Type: application/json
```

OpenAI-compatible text-to-speech. Only cloud engines support TTS; requesting a local-only engine returns `400`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `input` | string | yes | Text to synthesise |
| `model` | string | no | Provider model override |
| `voice` | string | no | Voice identifier |
| `response_format` | string | no | `"mp3"` (default), `"opus"`, `"aac"`, `"flac"`, `"wav"` |
| `speed` | float | no | Playback speed multiplier (provider-dependent) |
| `engine` | string | no | Engine name (`"openai"`, `"elevenlabs"`, `"deepgram"`) |
| `private` | bool | no | Always `false` for TTS (cloud only) |

Returns raw audio bytes with the appropriate `Content-Type` header.

---

### Profiles

Postprocessing profiles are JSON files that bundle an `initial_prompt` list and an ordered pipeline
of steps (replacements, LLM formatting, structured extraction). Named profiles are stored in
`RESONA_PROFILES_DIR` on the server. The bundled `default` profile applies German dictation
replacements automatically.

See [Postprocessing Profiles](../guide/postprocessing.md) for the profile file format.

#### List profiles

```
GET /profiles/
```

Returns an array of profile summaries:

```json
[
  {"name": "default", "description": "Bundled German medical dictation defaults"},
  {"name": "medical-de", "description": "German medical dictation with LLM formatting"}
]
```

#### Get profile

```
GET /profiles/{name}
```

Returns the full profile JSON. Returns `404` if not found.

#### Create or replace profile

```
PUT /profiles/{name}
Content-Type: application/json
```

Upload a profile object. The `name` field in the body must match the URL `{name}`. Returns the
stored profile.

#### Delete profile

```
DELETE /profiles/{name}
```

Returns `{"ok": true}`. Returns `404` if not found.

---

## Testbench

Contributed recordings and scans, references and bench runs. Disabled by default: with `testbench_enabled=false` every route except `/status` answers `403 testbench_disabled`. `hub_operator` credentials are always rejected. See [Testbench](../guide/testbench.md).

| Method & path | Role | Description |
|---|---|---|
| `GET /v1/testbench/status` | client, org_admin | `{enabled, items, corrected, runs}` |
| `GET /v1/testbench/candidates` | org_admin | `{items: [{kind, candidate, workers_online}]}`: tuples approved and tested by ≥1 approved private worker |
| `POST /v1/testbench/items` | client, org_admin | Multipart `file` (+ optional `language`, `label`). Persists the original, queues the draft run, `201` ItemDetail. `415` unsupported media, `422` empty, `413` over `RESONA_MAX_UPLOAD_BYTES` |
| `GET /v1/testbench/items` | org_admin | `?kind=&status=&limit=&cursor=` → `{items, next_cursor}`, newest first |
| `GET /v1/testbench/items/{id}` | org_admin | ItemDetail: summary + `sha256`, `reference`, `draft`, `runs` |
| `GET /v1/testbench/items/{id}/file` | org_admin | Original bytes (`inline`, `nosniff`, `no-store`); `410 content_unavailable` if the file is gone |
| `PUT /v1/testbench/items/{id}/reference` | org_admin | Body `{"text": "..."}` (1–1,000,000 chars); sets the reference and re-scores completed runs |
| `DELETE /v1/testbench/items/{id}` | org_admin | `204`; removes row, files, runs and cancels active jobs |
| `POST /v1/testbench/items/{id}/runs` | org_admin | Body `{"candidates": [Candidate, ...]}` (1–16); `422 unavailable_candidate` for a candidate not in `/candidates`; an identical active run is skipped. `201` `{items: [RunSummary]}` |
| `GET /v1/testbench/items/{id}/runs/{run_id}` | org_admin | RunDetail: `text`, `warnings`, `alignment` (`null` without reference or before completion) |
| `DELETE /v1/testbench/items/{id}/runs/{run_id}` | org_admin | `204`; cancels the job if active |
| `GET /v1/testbench/leaderboard` | org_admin | `{corrected_items, items: [{kind, candidate, items, wer_mean, cer_mean, seconds_mean}]}` over completed **bench** runs on corrected items (draft runs excluded), best WER first |

Item `status` is `awaiting_draft`, `needs_correction` or `corrected`; run `status` is `queued`, `processing`, `completed`, `failed` or `cancelled`; run `role` is `draft` or `bench`. Candidates have the job-core shape `{engine, model, model_revision, accelerator, ocr_engine}`. Audio runs are scored on `raw_text` with `text-v1`, documents on `markdown` with `markdown-v1`.

---

## Error responses

| Status | Meaning |
|--------|---------|
| 400 | Bad request (unknown engine, capability not supported, bad input) |
| 404 | Resource not found |
| 409 | Conflict (duplicate replacement or prompt; no engine available) |
| 415 | Unsupported media type |
| 503 | Engine unavailable; or cloud API key not set |
| 502 | Cloud provider returned an error |

Error body:

```json
{"detail": "human-readable message"}
```
