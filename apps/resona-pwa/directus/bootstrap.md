# Directus bootstrap

One-time setup for the Resona platform backend.

## Automated (default)

`docker compose up -d` runs the **`directus-bootstrap`** one-shot service
(`directus/bootstrap.mjs`) once Directus is healthy. It is idempotent — it
checks before every step and re-runs harmlessly — and brings a blank instance
to exactly the state documented below:

- the `recordings` + `transcripts` + `transcript_corrections` + `recording_parts` +
  `documents` collections, fields, and M2O
  relations (§1). Fields missing on an existing install (e.g. a volume created
  before `hub_job_id` / `documents` existed) are added and logged as
  `+ field <collection>.<field>`;
- the non-admin **`user`** role + `user-policy` + owner-scoped permissions (§2),
  plus `directus_files` upload/read/update for the owner (needed for audio and
  document files);
- a check that the **create ownership guard** extension
  `directus-extension-resona-owner-guard` (≥ 1.1.0) is loaded and enabled (§2): a
  user may only create a transcript, correction or recording part on their own
  recording, and a part only with their own audio file. The bootstrap stops
  (`✗ extension … is not loaded` / `… predates recording_parts`) before touching any
  permission when it is missing or too old;
- the custom field **`directus_users.hub_token`** (string, hidden) plus `user`-policy
  `directus_users` read/update restricted to `id = $CURRENT_USER` and the fields
  `id`/`hub_token`: the user's own Resona hub token, synced across their devices by
  the PWA (directus mode). Other users' rows and fields stay unreadable; admins are
  unaffected;
- the **`transcribe-worker`** admin user whose static token is **pinned to
  `DIRECTUS_TOKEN`** from `.env` — so the worker authenticates with no
  copy-paste (§3). `DIRECTUS_TOKEN` must be set (e.g. `openssl rand -hex 24`);
  compose refuses to start the bootstrap if it's empty.

The admin user itself is seeded by the Directus image from
`DIRECTUS_ADMIN_EMAIL` / `DIRECTUS_ADMIN_PASSWORD` on first boot (empty DB only).

Run it standalone against a running Directus with:

    DIRECTUS_URL=http://localhost:7700 \
    ADMIN_EMAIL=… ADMIN_PASSWORD=… DIRECTUS_TOKEN=… \
    node directus/bootstrap.mjs

The sections below document **what the script does** (and the manual UI path if
you ever need it). `bootstrap.mjs` is the source of truth for the live setup;
this prose and `schema-snapshot.yaml` are reference.

## 1. Collections

Create the collections (Settings → Data Model), or apply the committed
snapshot (§4).

### `recordings`
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | primary key |
| user_created | M2O → directus_users | "User Created" special field |
| date_created | timestamp | "Date Created" special field |
| date_updated | timestamp | "Date Updated" special field — Directus bumps it on every update (e.g. the worker's `claim` PATCH); the worker's stale-claim recovery filters on it, so it MUST be present |
| title | string | |
| title_auto | boolean | default `false` — `title` is a date placeholder; the project-worker replaces it with a title derived from the finished transcript and clears the flag. User may create/update it (a rename sends `false`) |
| audio_file | M2O → directus_files | |
| duration_seconds | float | |
| language | string | default `de` |
| profile | string | default `default` |
| status | string (dropdown) | pending / transcribing / done / error; default `pending` |
| error_message | text | nullable |
| source | string (dropdown) | batch / live; default `batch` |
| review_status | string (dropdown) | editing / ready; default `editing` — user review workflow, independent of `status` |
| hub_job_id | integer | nullable — durable Resona hub job id set by the project-worker while the job runs; rows with it set are never reclaimed as stale |

### `transcripts`
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | primary key |
| recording | M2O → recordings | one per recording |
| text | text | |
| structured | json | nullable |
| segments | json | nullable |
| engine | string | |
| date_created / date_updated | timestamp | special fields |
| provenance | json | nullable — hub `JobResult.provenance` (expert mode) |
| raw_text | text | nullable — the raw transcript a postprocess profile ran on ("Nachbearbeiten"); null while `text` is raw |
| applied_profile | string | nullable — profile whose output `text` is |
| processed_text | text | nullable — that profile's output as delivered (the PWA asks before a re-run/restore replaces edits made since) |
| postprocess_status | string | nullable — pending / processing / done / error; the PWA sets `pending` + `postprocess_profile`, the project-worker runs the hub text job |
| postprocess_profile | string | nullable — requested hub profile name |
| postprocess_error | text | nullable — German message of a failed run |
| postprocess_queue_reason | string | nullable — hub `queue_reason` while the text job waits (`no_capable_worker`, …) |
| postprocess_job_id / postprocess_job_key | integer / string | nullable — in-flight hub text job and its Idempotency-Key (like `hub_job_*` on recordings) |

### `documents`
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | primary key |
| user_created | M2O → directus_users | "User Created" special field |
| date_created / date_updated | timestamp | special fields (stale recovery filters on `date_updated`) |
| title | string | |
| file | M2O → directus_files | PDF / PNG / JPEG / TIFF |
| status | string | pending / processing / done / error; default `pending` |
| error_message | text | nullable |
| markdown | text | nullable — hub result |
| pages | json | nullable — hub result pages |
| hub_job_id | integer | nullable — durable hub job id (project-worker) |

### `recording_parts`
A re-dictated part of a recording (ticket #48): the PWA records it, the
project-worker transcribes it as a hub job, the PWA lands the text in the
transcript and appends the audio to the recording.

| Field | Type | Notes |
|-------|------|-------|
| id | UUID | primary key — chosen by the PWA on create (the editor's part chip refers to it before the row exists) |
| user_created | M2O → directus_users | "User Created" special field |
| date_created / date_updated | timestamp | special fields (stale recovery filters on `date_updated`) |
| recording | M2O → recordings | ON DELETE CASCADE |
| seq | integer | order within the recording, 1..n |
| status | string | pending → transcribing (worker claim) → done (worker wrote `text`) → applied (PWA landed it); error (worker failure, `error_message`); discarded (user gave the part up). Retry: error → pending. Default `pending` |
| audio | M2O → directus_files | ON DELETE SET NULL — the part's own audio; the worker deletes the file and clears the field once `offset_seconds` is set and the part is done/applied/discarded |
| language / profile | string | copied from the recording at creation; used for the hub job |
| offset_seconds / duration_seconds | float | nullable — range of the part in the recording's merged audio; null until the PWA appended it |
| anchor_offset | integer | nullable — insertion point in the saved transcript (null = end) |
| anchor_text_sha | string | SHA-256 hex of the transcript text `anchor_offset` refers to |
| text | text | nullable — hub result (Markdown), worker only |
| error_message | text | nullable |
| hub_job_id / hub_job_key | integer / string | nullable — in-flight hub job and its Idempotency-Key (like on recordings) |

## 2. Roles & access policy

Create a role **"user"** (non-admin). Add a policy with:
- `recordings`, `documents`: read/create/update/delete where `user_created = $CURRENT_USER`
- `transcripts`: read/update/delete where `recording.user_created = $CURRENT_USER`;
  create without an item rule (see below)
- `transcript_corrections`: read own (`user_created`), create (see below); never
  updated or deleted by users
- `recording_parts`: read/update/delete where `recording.user_created =
  $CURRENT_USER`; create without an item rule (see below) with the fields `id`
  (client-chosen uuid), `recording`, `seq`, `status`, `audio`, `language`, `profile`, `anchor_offset`,
  `anchor_text_sha` and validation `status = pending`; update only `status`,
  `anchor_offset`, `anchor_text_sha`, `offset_seconds`, `duration_seconds` with
  `status` in pending/applied/discarded. `text`, `error_message` and the hub job
  fields are the project-worker's
- create/update carry **explicit field lists**: the job bookkeeping (`hub_job_*`,
  `postprocess_job_*`, `postprocess_error`, `postprocess_queue_reason`) is the
  project-worker's, so a user can never point a row at another hub job of the
  organization. Users may write `postprocess_status` only as `pending` or null
  (validation). The bootstrap patches existing permissions whose fields/filters
  differ (`~ perm … updated`), so re-running it tightens older installs.

- `directus_users`: read (`id`, `hub_token`) and update (`hub_token`) where
  `id = $CURRENT_USER` — the user's own hub token, nothing else. Needs the custom
  field `hub_token` (string) on `directus_users`.

**Create ownership guard (hook extension).** Directus 11 ignores item rules
(`permissions`) on create and checks `validation` against the request payload, where
an M2O is a bare id: a relational validation such as `recording.user_created =
$CURRENT_USER` therefore rejected every create ("Value is required"), and an item rule
would let a user attach rows to someone else's recording. The create permissions
carry neither; the hook extension
`directus/extensions/directus-extension-resona-owner-guard` checks instead: on
`items.create` of `transcripts`, `transcript_corrections` and `recording_parts` by a
non-admin it requires the parent ids (`recording`; for corrections also `transcript`;
for parts also `audio`) and reads each parent with the caller's permissions; a
correction's transcript must also belong to its `recording`, a part's `audio` must be
a file the user uploaded (the project-worker transcribes it with admin rights). A
parent the user cannot read (someone else's, missing) aborts the create with 403 (no
parent id or a nested create: 400); a batch is rolled back as a whole. Admin tokens
(the project-worker) are not checked.

The extension is versioned code, baked into the Directus image by
`directus/Dockerfile.directus` (pinned to a Directus minor; all three compose files
build it), so it cannot be edited in the Data Studio and needs no bootstrap step. A
manual setup must run that image (or copy `directus/extensions/` into
`/directus/extensions/`). Upgrading Directus: bump the `FROM` line; the PWA CI job
`directus` builds the image and runs `directus/verify-permissions.mjs` (own, foreign,
missing parent, batch, correction, recording-part and worker cases) against it, which can also be run
against a local stack with the bootstrap's env.

The project-worker authenticates with a **static service token** (admin role),
which bypasses the user policy.

## 3. Service token for the worker

Create a dedicated user (admin role) "transcribe-worker" → generate a static
token (User detail → Token). Put it in `.env` as `DIRECTUS_TOKEN`.

## 4. Schema snapshot (reproducible deploys)

Export after authoring:

    docker compose exec directus \
      npx directus schema snapshot --yes /directus/schema-snapshot.yaml

This writes to the bind-mounted `./directus/schema-snapshot.yaml`. Commit it.
The snapshot only contains fields that have a `directus_fields` row, i.e. were
created with `meta`. `bootstrap.mjs` gives every field `meta` and backfills it on
older installs (`~ meta ...`), so run the bootstrap before exporting.

Apply on a fresh instance:

    docker compose exec directus \
      npx directus schema apply --yes /directus/schema-snapshot.yaml
