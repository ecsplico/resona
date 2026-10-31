# resona-api

resona-api is the central hub. It accepts uploads, keeps a durable, organization-scoped job queue and hands jobs to outbound-only pull workers (`resona-worker`). The hub runs no inference for queued jobs. It also exposes OpenAI-compatible `/v1/audio/*` routes.

- Default port: **7000**
- Database: SQLite at `$DATA_PATH/resona.db` (single hub replica)
- Upload limit: 200 MiB per upload (`RESONA_MAX_UPLOAD_BYTES`), enforced while streaming

## Authentication

Every request needs an org-bound credential: `Authorization: Bearer <token>`. REST routes also accept `X-API-Key: <token>`; sending both with different values is rejected. Missing, revoked or unknown token → `401`; wrong role → `403`. There is no auth-disabled mode.

The organization always comes from the credential, never from a request field or header. IDs that belong to another organization return `404`.

Issue tokens on the hub host with `resona-admin`. Each token is printed once; the hub stores only its SHA-256 hash.

```bash
resona-admin organization create acme
resona-admin credential issue --organization acme --role client     # or org_admin
resona-admin credential revoke <credential-id>
resona-admin worker enroll mac-studio --organization acme           # worker token, unapproved
resona-admin worker approve <worker-id> --private --capabilities-file caps.json
resona-admin worker revoke <worker-id>
```

Roles: `client` (submit and read jobs, read settings), `org_admin` (also change settings and profiles), `worker` (worker routes only).

Error bodies have the form `{"error": "<code>", "message": "<safe text>"}`.

## Endpoints

### Health

```
GET /health
```

Returns liveness status.

```json
{"status": "ok"}
```

---

### Uploads and jobs (roles `client`, `org_admin`)

Jobs are asynchronous: upload the file, create a job, then poll until it finishes. `ResonaClient.submit_job()` does the upload and the job creation in one call.

#### Upload a file

```
POST /v1/uploads
Content-Type: multipart/form-data   (field: file)
```

Returns `201 {"upload_id", "sha256", "size"}`. Empty file → `422 empty_upload`, unsupported media → `415 unsupported_media`, over 200 MiB → `413 too_large`. An upload that is not attached to a job expires after one hour. One upload can be used by exactly one job.

#### Create a job

```
POST /v1/jobs
Idempotency-Key: <unique key>
Content-Type: application/json
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `kind` | string | yes | `audio` or `document` |
| `upload_id` | string | yes | ID returned by `POST /v1/uploads` |
| `execution_profile` | string | no | Execution profile from the organization settings |
| `language` | string | no | Language code; defaults to the organization setting |
| `engine`, `model` | string | no | Override; must be an approved candidate (else `422`) |
| `profile` | string | no | Name of a postprocess profile stored on the hub |
| `options` | object | no | Audio: `translate`, `word_timestamps`, `vad_filter`. Document: `languages`, `ocr`, `ocr_engine`, `page_start`, `page_end`, `pipeline` |
| `private` | bool | no | Default `true`. `false` needs organization cloud approval, else `403 policy_denied` |
| `provenance` | object | no | `project_id`, `session_id`, `source` (informational only) |

Returns `202 {"job_id": 42, "status": "pending"}`. The `Idempotency-Key` header is required: the same key with the same request returns the same job; the same key with a different request returns `409 idempotency_conflict`.

#### List, get, cancel, delete

```
GET    /v1/jobs?cursor=<opaque>&limit=<1..100>   → {items: [JobView], next_cursor}
GET    /v1/jobs/{id}                             → JobView
GET    /v1/jobs/{id}/result                      → JobResult (409 not_ready, 410 expired)
POST   /v1/jobs/{id}/cancel                      → JobView
DELETE /v1/jobs/{id}                             → 204
```

JobView fields: `job_id`, `kind`, `status`, `queue_reason` (`no_capable_worker`, `waiting_capacity` or null), `private`, `execution_profile`, `candidate`, `attempt`, `error` (`{code, message}` or null), `result_available`, `created_at`, `updated_at`, `deadline_at`.

JobResult fields: `schema_version`, `kind`, `raw_text`, `markdown`, `language`, `segments`, `pages`, `warnings`, `provenance`.

#### Job state machine

```
pending → processing → completed | failed | cancelled
```

A worker claims a job under a 120-second lease. If the lease expires, the job returns to `pending`. A job fails after three assignments (`attempts_exhausted`) or 24 hours (`deadline_exceeded`). Poll `GET /v1/jobs/{id}` or use `ResonaClient.wait_for_job()`.

Retention: the input is deleted when the job reaches a terminal state, results after 24 hours, job metadata after 7 days.

---

### Worker routes (role `worker`)

Used only by `resona-worker`. Every call checks the organization, that the worker is approved and not revoked, and the current policy. Private jobs go only to workers approved with `--private`.

```
POST /v1/workers/heartbeat
POST /v1/workers/claim?wait=<0..25>
GET  /v1/workers/jobs/{id}/input        (X-Resona-Lease)
POST /v1/workers/jobs/{id}/heartbeat    (X-Resona-Lease)
POST /v1/workers/jobs/{id}/policy       (X-Resona-Lease)
POST /v1/workers/jobs/{id}/result       (X-Resona-Lease, X-Resona-Result-Sha256)
POST /v1/workers/jobs/{id}/fail         (X-Resona-Lease)
```

A call with an expired or replaced lease returns `409 stale_lease`. An incompatible worker protocol returns `426`.

---

### Settings

```
GET /v1/settings      → {organization_id, revision, settings} + ETag
PUT /v1/settings      (org_admin, If-Match: <revision>)
```

---


### Engine catalogue

#### List engines

```
GET /v1/engines
```

Returns every engine the gateway knows about — local engine-servers plus any cloud provider whose API key is set.

Response:

```json
{
  "engines": [
    {
      "name": "faster-whisper",
      "kind": "local",
      "capabilities": ["stt"],
      "private": true,
      "available": true,
      "models": ["large-v3"]
    },
    {
      "name": "deepgram",
      "kind": "cloud",
      "capabilities": ["stt", "tts"],
      "private": false,
      "available": true,
      "models": ["nova-3"]
    }
  ],
  "default": "faster-whisper"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Engine identifier used in `engine` fields |
| `kind` | string | `"local"` (engine-server) or `"cloud"` (provider) |
| `capabilities` | string[] | `"stt"` and/or `"tts"` |
| `private` | bool | `true` for local engines; cloud engines are always `false` |
| `available` | bool | Local: /health reachable. Cloud: API key set |
| `models` | string[] | Known model names for this engine |

Cloud providers (deepgram, openai, elevenlabs) appear automatically when their respective `*_API_KEY` env var is set on the server. No extra configuration needed.

---

### Audio (v1 — synchronous)

These endpoints are synchronous: they call the engine and return the result directly, without creating a job.

#### Transcribe

```
POST /v1/audio/transcriptions
Content-Type: multipart/form-data
```

OpenAI-compatible speech-to-text. A postprocessing profile is applied to the transcript before it is returned.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | file | yes | Audio file |
| `model` | string | no | Model override (passed to engine) |
| `language` | string | no | BCP-47 language code (default: `"de"`) |
| `prompt` | string | no | Initial prompt / vocabulary hint |
| `temperature` | float | no | Sampling temperature (engine-dependent) |
| `response_format` | string | no | `"json"` (default), `"text"`, or `"verbose_json"` |
| `engine` | string | no | Engine name, or `"local"` to require a local engine |
| `private` | bool | no | Refuse cloud engines and cloud postprocess steps (default: `true`); `false` needs organization cloud approval |
| `profile` | string | no | Organization profile name; omit to use the organization default |

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
